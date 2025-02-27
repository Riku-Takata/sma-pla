from flask import Blueprint, request, jsonify, redirect, url_for
import os
import json
import hmac
import hashlib
import time
import threading
import pytz
from datetime import datetime, timedelta
from slack_sdk import WebClient
from slack_sdk.signature import SignatureVerifier
from slack_sdk.errors import SlackApiError

from models import User, UserPlatformLink, db
from utils.nlp_parser import parse_schedule_from_text
from utils.calendar_handler import (
    create_calendar_event, check_schedule_conflicts, 
    get_authorization_url, find_next_available_time
)

# Slack API設定
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5001")

# メッセージ履歴取得数の上限
MESSAGE_HISTORY_LIMIT = 10

# キャッシュ辞書（一時的に予定情報を保存）
schedule_cache = {}

# Blueprintの作成
slack_bp = Blueprint('slack', __name__, url_prefix='/webhook/slack')
slack_client = WebClient(token=SLACK_BOT_TOKEN)
signature_verifier = SignatureVerifier(SLACK_SIGNING_SECRET)

def verify_slack_request(request):
    """Slackからのリクエストを検証する"""
    if not SLACK_SIGNING_SECRET:
        print("Warning: SLACK_SIGNING_SECRET is not set. Request verification skipped.")
        return True
        
    return signature_verifier.is_valid_request(
        body=request.get_data().decode('utf-8'),
        timestamp=request.headers.get('X-Slack-Request-Timestamp', ''),
        signature=request.headers.get('X-Slack-Signature', '')
    )

@slack_bp.route('/events', methods=['POST'])
def slack_events():
    """Slackからのイベントを処理する"""
    # リクエスト検証（本番環境では必ず有効にする）
    if not verify_slack_request(request):
        return jsonify({"error": "Invalid request"}), 403

    data = request.json

    # URL検証チャレンジ（Slack App設定時に必要）
    if data and data.get('type') == 'url_verification':
        return jsonify({"challenge": data.get('challenge')})

    # イベントハンドリング
    if data and data.get('event'):
        # バックグラウンドで処理するためにスレッドを起動
        thread = threading.Thread(target=process_event, args=(data.get('event'),))
        thread.start()

    return jsonify({"status": "ok"})

@slack_bp.route('/command', methods=['POST'])
def slack_commands():
    """Slackのスラッシュコマンド（/plan）を処理する"""
    # リクエスト検証
    if not verify_slack_request(request):
        return jsonify({"error": "Invalid request"}), 403

    # フォームデータの取得
    form_data = request.form
    command = form_data.get('command')
    text = form_data.get('text', '')
    channel_id = form_data.get('channel_id')
    user_id = form_data.get('user_id')
    trigger_id = form_data.get('trigger_id')
    
    print(f"Received command: {command} from user: {user_id} in channel: {channel_id}")

    # /plan コマンドの処理
    if command == '/plan':
        # バックグラウンドで処理するためにスレッドを起動
        thread = threading.Thread(
            target=process_plan_command, 
            args=(channel_id, user_id, text, trigger_id)
        )
        thread.start()
        
        # 即時応答（Slackの3秒タイムアウトを回避）
        return jsonify({
            "response_type": "ephemeral",
            "text": "会話から予定を検出しています..."
        })
    
    return jsonify({
        "response_type": "ephemeral",
        "text": "不明なコマンドです。"
    })

@slack_bp.route('/interactive', methods=['POST'])
def slack_interactive():
    """Slackのインタラクティブコンポーネント（ボタンなど）を処理する"""
    # リクエスト検証
    if not verify_slack_request(request):
        return jsonify({"error": "Invalid request"}), 403

    # ペイロードの取得
    payload = json.loads(request.form.get('payload', '{}'))
    
    action_id = None
    if payload.get('actions') and len(payload.get('actions')) > 0:
        action_id = payload['actions'][0].get('action_id')
    
    user_id = payload.get('user', {}).get('id')
    channel_id = payload.get('channel', {}).get('id')
    
    # バックグラウンドで処理
    thread = threading.Thread(
        target=process_interactive_action, 
        args=(action_id, payload, user_id, channel_id)
    )
    thread.start()
    
    # 即時応答
    return jsonify({"response_action": "clear"})

def process_event(event):
    """イベントを非同期で処理する"""
    # メッセージイベントのみ処理
    if event.get('type') != 'message' or event.get('subtype') == 'bot_message':
        return
    
    # ここでメッセージを保存する処理を実装できる
    # 現在はスラッシュコマンドでのみ会話履歴を取得

def process_plan_command(channel_id, user_id, text, trigger_id):
    """'/plan'コマンドを処理する"""
    try:
        # ユーザー情報の取得・作成
        user_link = UserPlatformLink.query.filter_by(
            platform_name='slack',
            platform_user_id=user_id
        ).first()
        
        if user_link:
            # 既存ユーザー
            user = user_link.user
        else:
            # 新規ユーザー作成
            try:
                # Slackからユーザー情報を取得
                slack_user_info = slack_client.users_info(user=user_id)
                display_name = slack_user_info['user']['real_name']
                email = slack_user_info['user'].get('profile', {}).get('email')
            except SlackApiError:
                display_name = f"SlackUser-{user_id[:8]}"
                email = None
            
            user = User(display_name=display_name, email=email)
            db.session.add(user)
            db.session.flush()  # IDを生成するためにフラッシュ
            
            # プラットフォームリンクを作成
            user_link = UserPlatformLink(
                user_id=user.id,
                platform_name='slack',
                platform_user_id=user_id
            )
            db.session.add(user_link)
            db.session.commit()
        
        # Google Calendar連携確認
        if not user.google_refresh_token:
            # 未連携の場合、認証リンクを送信
            auth_url, state = get_authorization_url()
            
            slack_client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "予定を自動登録するには、Googleアカウントとの連携が必要です。"
                        }
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "text": "Googleアカウントと連携する"
                                },
                                "url": auth_url,
                                "action_id": "oauth_link"
                            }
                        ]
                    }
                ]
            )
            return
        
        # テキストが指定されている場合はそれを直接解析
        if text.strip():
            # 指定されたテキストから予定を解析
            schedule_info = parse_schedule_from_text(text)
            
            if schedule_info['confidence'] >= 0.5 and schedule_info['start_datetime']:
                # 予定情報が取得できた場合
                show_schedule_confirmation(user.id, channel_id, user_id, schedule_info)
                return
        
        # 最近の会話を取得
        messages = get_conversation_history(channel_id)
        
        if not messages:
            slack_client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="最近の会話から予定を検出できませんでした。直接予定の詳細を入力してください。\n例: `/plan 明日15時からミーティング`"
            )
            return
        
        # 会話を結合して解析
        combined_text = ""
        for msg in messages:
            if not msg.get('text'):
                continue
            sender = f"<@{msg.get('user', 'unknown')}>"
            combined_text += f"{sender}: {msg.get('text')}\n"
        
        # 予定情報の解析
        schedule_info = parse_schedule_from_text(combined_text)
        
        # 解析結果に応じた処理
        if schedule_info['confidence'] >= 0.5 and schedule_info['start_datetime']:
            # 予定情報が取得できた場合
            show_schedule_confirmation(user.id, channel_id, user_id, schedule_info)
        else:
            # 解析失敗時
            slack_client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="会話から予定情報を検出できませんでした。\n" + 
                     "具体的な予定を指定してみてください。例:\n" +
                     "`/plan 明日の15時から1時間、会議室でミーティング`"
            )
    
    except Exception as e:
        print(f"Error processing plan command: {e}")
        try:
            slack_client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"エラーが発生しました: {str(e)}"
            )
        except:
            pass

def process_interactive_action(action_id, payload, user_id, channel_id):
    """インタラクティブアクションを処理する"""
    try:
        if not action_id:
            return
        
        user_link = UserPlatformLink.query.filter_by(
            platform_name='slack',
            platform_user_id=user_id
        ).first()
        
        if not user_link:
            slack_client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="ユーザー情報が見つかりませんでした。もう一度お試しください。"
            )
            return
        
        user = user_link.user
        
        # アクションによって処理を分岐
        if action_id == 'confirm_schedule':
            # キャッシュから予定情報を取得
            cache_key = f"{user_id}_{channel_id}"
            schedule_info = schedule_cache.get(cache_key)
            
            if not schedule_info:
                slack_client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="予定情報が見つかりませんでした。もう一度お試しください。"
                )
                return
            
            # ダブルブッキングのチェック
            has_conflict, conflicts = check_schedule_conflicts(
                user.id,
                schedule_info['start_datetime'],
                schedule_info['end_datetime']
            )
            
            if isinstance(has_conflict, bool) and has_conflict:
                # 重複がある場合は確認メッセージを表示
                conflict_info = "\n".join([
                    f"・{event['summary']} ({format_datetime(event['start'])})"
                    for event in conflicts[:3]  # 最大3件表示
                ])
                
                # 次の空き時間を検索
                next_available = find_next_available_time(
                    user.id,
                    schedule_info['start_datetime'], 
                    int((schedule_info['end_datetime'] - schedule_info['start_datetime']).total_seconds() // 60)
                )
                
                blocks = [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"⚠️ *予定が重複しています*\n{conflict_info}"
                        }
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "text": "それでも登録する"
                                },
                                "style": "danger",
                                "action_id": "force_schedule"
                            },
                            {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "text": "キャンセル"
                                },
                                "action_id": "cancel_schedule"
                            }
                        ]
                    }
                ]
                
                # 代替時間があれば提案する
                if next_available:
                    next_start = next_available
                    next_end = next_start + (schedule_info['end_datetime'] - schedule_info['start_datetime'])
                    
                    # キャッシュに代替時間の予定を保存
                    alternative_schedule = schedule_info.copy()
                    alternative_schedule['start_datetime'] = next_start
                    alternative_schedule['end_datetime'] = next_end
                    schedule_cache[f"{cache_key}_alternative"] = alternative_schedule
                    
                    blocks.insert(1, {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"📅 別の時間はいかがですか？\n*{format_datetime(next_start)}* から"
                        }
                    })
                    
                    blocks[2]["elements"].insert(0, {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "代替時間で登録"
                        },
                        "style": "primary",
                        "action_id": "use_alternative_time"
                    })
                
                slack_client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    blocks=blocks
                )
                return
            
            # 重複がない場合は直接登録
            register_calendar_event(user.id, schedule_info, channel_id, user_id)
            
        elif action_id == 'force_schedule':
            # 重複を承知で予定を登録
            cache_key = f"{user_id}_{channel_id}"
            schedule_info = schedule_cache.get(cache_key)
            
            if schedule_info:
                register_calendar_event(user.id, schedule_info, channel_id, user_id, force=True)
            else:
                slack_client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="予定情報が見つかりませんでした。もう一度お試しください。"
                )
        
        elif action_id == 'use_alternative_time':
            # 代替時間で予定を登録
            cache_key = f"{user_id}_{channel_id}_alternative"
            schedule_info = schedule_cache.get(cache_key)
            
            if schedule_info:
                register_calendar_event(user.id, schedule_info, channel_id, user_id)
            else:
                slack_client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="代替時間の情報が見つかりませんでした。もう一度お試しください。"
                )
        
        elif action_id == 'cancel_schedule':
            # 予定登録のキャンセル
            slack_client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="予定の登録をキャンセルしました。"
            )
            
            # キャッシュをクリア
            cache_key = f"{user_id}_{channel_id}"
            if cache_key in schedule_cache:
                del schedule_cache[cache_key]
            if f"{cache_key}_alternative" in schedule_cache:
                del schedule_cache[f"{cache_key}_alternative"]
    
    except Exception as e:
        print(f"Error processing interactive action: {e}")
        try:
            slack_client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"エラーが発生しました: {str(e)}"
            )
        except:
            pass

def get_conversation_history(channel_id, limit=MESSAGE_HISTORY_LIMIT):
    """会話履歴を取得する"""
    try:
        response = slack_client.conversations_history(
            channel=channel_id,
            limit=limit
        )
        return response.get('messages', [])
    except SlackApiError as e:
        print(f"Error getting conversation history: {e}")
        return []

def show_schedule_confirmation(user_id, channel_id, slack_user_id, schedule_info):
    """予定の確認ダイアログを表示する"""
    try:
        # キャッシュに予定情報を一時保存
        cache_key = f"{slack_user_id}_{channel_id}"
        schedule_cache[cache_key] = schedule_info
        
        # 予定の詳細を表示
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "予定を検出しました"
                }
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*タイトル:*\n{schedule_info['title']}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*日時:*\n{format_datetime(schedule_info['start_datetime'])} - {format_time(schedule_info['end_datetime'])}"
                    }
                ]
            }
        ]
        
        # 場所があれば追加
        if schedule_info['location']:
            blocks[1]["fields"].append({
                "type": "mrkdwn",
                "text": f"*場所:*\n{schedule_info['location']}"
            })
        
        # 確認ボタンを追加
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "カレンダーに登録"
                    },
                    "style": "primary",
                    "action_id": "confirm_schedule"
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "キャンセル"
                    },
                    "action_id": "cancel_schedule"
                }
            ]
        })
        
        slack_client.chat_postEphemeral(
            channel=channel_id,
            user=slack_user_id,
            blocks=blocks
        )
    except Exception as e:
        print(f"Error showing schedule confirmation: {e}")
        slack_client.chat_postEphemeral(
            channel=channel_id,
            user=slack_user_id,
            text=f"予定の確認表示中にエラーが発生しました: {str(e)}"
        )

def register_calendar_event(user_id, schedule_info, channel_id, slack_user_id, force=False):
    """カレンダーに予定を登録する"""
    try:
        # 予定をGoogle Calendarに登録
        success, result = create_calendar_event(user_id, schedule_info)
        
        if success:
            # 登録成功メッセージ
            blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "✅ *予定を登録しました*"
                    }
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*タイトル:*\n{schedule_info['title']}"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*日時:*\n{format_datetime(schedule_info['start_datetime'])} - {format_time(schedule_info['end_datetime'])}"
                        }
                    ]
                }
            ]
            
            # 場所があれば追加
            if schedule_info['location']:
                blocks[1]["fields"].append({
                    "type": "mrkdwn",
                    "text": f"*場所:*\n{schedule_info['location']}"
                })
            
            # 重複強制登録の場合はその旨を追加
            if force:
                blocks.insert(1, {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "⚠️ _重複する予定があることを承知の上で登録されました_"
                    }
                })
            
            # Googleカレンダーへのリンクを追加
            blocks.append({
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"<{result}|Googleカレンダーで確認>"
                    }
                ]
            })
            
            slack_client.chat_postEphemeral(
                channel=channel_id,
                user=slack_user_id,
                blocks=blocks
            )
            
            # キャッシュをクリア
            cache_key = f"{slack_user_id}_{channel_id}"
            if cache_key in schedule_cache:
                del schedule_cache[cache_key]
            if f"{cache_key}_alternative" in schedule_cache:
                del schedule_cache[f"{cache_key}_alternative"]
        else:
            # 登録失敗
            slack_client.chat_postEphemeral(
                channel=channel_id,
                user=slack_user_id,
                text=f"❌ 予定の登録に失敗しました: {result}"
            )
    except Exception as e:
        print(f"Error registering calendar event: {e}")
        slack_client.chat_postEphemeral(
            channel=channel_id,
            user=slack_user_id,
            text=f"予定の登録中にエラーが発生しました: {str(e)}"
        )

def format_datetime(dt):
    """日時をフォーマットする"""
    if isinstance(dt, str):
        return dt
    jst = pytz.timezone('Asia/Tokyo')
    if dt.tzinfo is None:
        dt = jst.localize(dt)
    elif dt.tzinfo != jst:
        dt = dt.astimezone(jst)
    
    weekdays = ["月", "火", "水", "木", "金", "土", "日"]
    weekday = weekdays[dt.weekday()]
    
    return f"{dt.year}年{dt.month}月{dt.day}日({weekday}) {dt.hour:02d}:{dt.minute:02d}"

def format_time(dt):
    """時刻のみをフォーマットする"""
    if isinstance(dt, str):
        return dt
    jst = pytz.timezone('Asia/Tokyo')
    if dt.tzinfo is None:
        dt = jst.localize(dt)
    elif dt.tzinfo != jst:
        dt = dt.astimezone(jst)
    
    return f"{dt.hour:02d}:{dt.minute:02d}"