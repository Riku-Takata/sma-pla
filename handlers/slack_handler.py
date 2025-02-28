from flask import Blueprint, request, jsonify
import os
import json
import threading
import pytz
from datetime import datetime
from slack_sdk import WebClient
from slack_sdk.signature import SignatureVerifier
from slack_sdk.errors import SlackApiError

from models import User, UserPlatformLink, db
from utils.message_parser import parse_user_input_for_scheduling
from utils.calendar_handler import (
    create_calendar_event, check_schedule_conflicts,
    get_authorization_url, find_next_available_time, exchange_code_for_token
)

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5001")

MESSAGE_HISTORY_LIMIT = 10

# キャッシュ辞書（一時的に予定情報を保存）: { "slackUserId_channelId" : {...schedule_info...} }
schedule_cache = {}

slack_bp = Blueprint('slack', __name__, url_prefix='/webhook/slack')
slack_client = WebClient(token=SLACK_BOT_TOKEN)
signature_verifier = SignatureVerifier(SLACK_SIGNING_SECRET)

def verify_slack_request(request):
    """Slackからのリクエストを検証する"""
    if not SLACK_SIGNING_SECRET:
        print("Warning: SLACK_SIGNING_SECRET is not set. Request verification skipped.")
        return True
    
    return signature_verifier.is_valid(
        request.get_data().decode('utf-8'),
        request.headers.get('X-Slack-Request-Timestamp', ''),
        request.headers.get('X-Slack-Signature', '')
    )

@slack_bp.route('/events', methods=['POST'])
def slack_events():
    """Slackのイベント(メッセージなど)を受け取る。サンプルでは未使用。"""
    if not verify_slack_request(request):
        return jsonify({"error": "Invalid request"}), 403

    data = request.json
    if data and data.get('type') == 'url_verification':
        # Slack App設定時のURL検証
        return jsonify({"challenge": data.get('challenge')})
    
    return jsonify({"status": "ok"})

@slack_bp.route('/command', methods=['POST'])
def slack_commands():
    """Slackのスラッシュコマンド (/plan) を処理する"""
    if not verify_slack_request(request):
        return jsonify({"error": "Invalid request"}), 403

    form_data = request.form
    command = form_data.get('command')
    text = form_data.get('text', '')
    channel_id = form_data.get('channel_id')
    user_id = form_data.get('user_id')

    if command == '/plan':
        from flask import current_app
        app = current_app._get_current_object()

        thread = threading.Thread(
            target=process_plan_command_with_app_context,
            args=(app, channel_id, user_id, text)
        )
        thread.start()

        # Slackは3秒以内に応答が必要
        return jsonify({
            "response_type": "ephemeral",
            "text": "会話から予定を解析しています..."
        })

    return jsonify({
        "response_type": "ephemeral",
        "text": "不明なコマンドです。"
    })

def process_plan_command_with_app_context(app, channel_id, user_id, text):
    with app.app_context():
        process_plan_command(channel_id, user_id, text)

def process_plan_command(channel_id, user_id, text):
    """
    1) テキスト or 最近の会話から予定を解析
    2) 結果を Slack に表示
    3) 未認証なら認証誘導、認証済みなら登録ボタン
    """
    # ユーザー取得または作成
    user_link = UserPlatformLink.query.filter_by(
        platform_name='slack',
        platform_user_id=user_id
    ).first()
    if user_link:
        user = user_link.user
    else:
        # 新規ユーザー作成
        try:
            slack_user_info = slack_client.users_info(user=user_id)
            display_name = slack_user_info['user']['real_name']
            email = slack_user_info['user'].get('profile', {}).get('email')
        except SlackApiError:
            display_name = f"SlackUser-{user_id[:8]}"
            email = None

        user = User(display_name=display_name, email=email)
        db.session.add(user)
        db.session.flush()

        user_link = UserPlatformLink(
            user_id=user.id,
            platform_name='slack',
            platform_user_id=user_id
        )
        db.session.add(user_link)
        db.session.commit()

    # 予定を解析
    schedule_info = None
    if text.strip():
        schedule_info = parse_user_input_for_scheduling(text)
    else:
        messages = get_conversation_history(channel_id, MESSAGE_HISTORY_LIMIT)
        if messages:
            combined_text = "\n".join(m.get('text', '') for m in messages if m.get('text'))
            schedule_info = parse_user_input_for_scheduling(combined_text)

    if not schedule_info or schedule_info.get('confidence', 0) < 0.5:
        slack_client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text="予定情報を検出できませんでした。日時や場所などを含めて入力してください。"
        )
        return

    # 解析結果をキャッシュ
    cache_key = f"{user_id}_{channel_id}"
    schedule_cache[cache_key] = schedule_info

    # 表示用ブロック
    blocks = create_schedule_blocks(schedule_info)

    # 認証済みかチェック
    if user.google_refresh_token:
        # 既にGoogle認証済み → 登録ボタン
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "今すぐカレンダーに登録"},
                    "style": "primary",
                    "action_id": "confirm_schedule"
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "キャンセル"},
                    "action_id": "cancel_schedule"
                }
            ]
        })
    else:
        # 未認証 → 認証URL + コード入力モーダルボタン
        auth_url, _ = get_authorization_url()
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "Googleカレンダーが未連携です。認証してください。"
            }
        })
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Googleアカウントと連携する"},
                    "url": auth_url,
                    "action_id": "oauth_link"
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "認証コードを入力する"},
                    "action_id": "open_auth_code_modal"
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "キャンセル"},
                    "action_id": "cancel_schedule"
                }
            ]
        })

    # Ephemeralメッセージ
    slack_client.chat_postEphemeral(
        channel=channel_id,
        user=user_id,
        text="予定を解析しました。",
        blocks=blocks
    )

def get_conversation_history(channel_id, limit):
    try:
        resp = slack_client.conversations_history(channel=channel_id, limit=limit)
        return resp.get('messages', [])
    except SlackApiError as e:
        print(f"Error getting conversation history: {e}")
        return []

def create_schedule_blocks(schedule_info):
    """解析した予定を表示するためのBlocks"""
    start_dt = format_datetime(schedule_info['start_datetime'])
    end_dt = format_time(schedule_info['end_datetime'])
    title = schedule_info['title']
    location = schedule_info.get('location') or "（場所指定なし）"

    blocks = [
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*タイトル:*\n{title}"
                },
                {
                    "type": "mrkdwn",
                    "text": f"*日時:*\n{start_dt} - {end_dt}"
                },
                {
                    "type": "mrkdwn",
                    "text": f"*場所:*\n{location}"
                }
            ]
        }
    ]
    return blocks

@slack_bp.route('/interactive', methods=['POST'])
def slack_interactive():
    """ボタンやモーダル送信などインタラクティブイベントを受け取る"""
    if not verify_slack_request(request):
        return jsonify({"error": "Invalid request"}), 403

    payload = json.loads(request.form.get('payload', '{}'))
    action_id = None
    if payload.get('actions') and len(payload['actions']) > 0:
        action_id = payload['actions'][0].get('action_id')
    
    user_id = payload.get('user', {}).get('id')
    # 注意: view_submission では payload["channel"] が無いことが多い
    # block_actions なら payload["channel"]["id"] が存在するが、安全策として get する
    channel_id = payload.get('channel', {}).get('id')

    from flask import current_app
    app = current_app._get_current_object()

    thread = threading.Thread(
        target=process_interactive_action_with_app_context,
        args=(app, action_id, payload, user_id, channel_id)
    )
    thread.start()

    return jsonify({"response_action": "clear"})

def process_interactive_action_with_app_context(app, action_id, payload, user_id, channel_id):
    with app.app_context():
        process_interactive_action(action_id, payload, user_id, channel_id)

def process_interactive_action(action_id, payload, user_id, channel_id):
    """
    実際にアクションを処理する関数
    """
    try:
        # 1) view_submission (モーダル送信)
        if payload.get("type") == "view_submission":
            callback_id = payload["view"].get("callback_id")
            if callback_id == "auth_code_modal_submit":
                # private_metadata から channel_id を復元
                meta_str = payload["view"].get("private_metadata", "{}")
                meta = json.loads(meta_str)
                if not channel_id:
                    # view_submissionの場合 channel_id は通常無いので
                    channel_id = meta.get("channel_id")

                # 認証コードを取得
                state_values = payload["view"]["state"]["values"]
                auth_code = state_values["auth_code_input_block"]["auth_code_input"]["value"].strip()

                # ユーザーを取得
                user_link = UserPlatformLink.query.filter_by(
                    platform_name='slack',
                    platform_user_id=user_id
                ).first()
                if not user_link:
                    return {
                        "response_action": "errors",
                        "errors": {
                            "auth_code_input_block": "ユーザー情報が見つかりません。"
                        }
                    }
                user_obj = user_link.user

                # コードをトークンに交換
                success, token_data = exchange_code_for_token(auth_code)
                if not success:
                    return {
                        "response_action": "errors",
                        "errors": {
                            "auth_code_input_block": f"認証コードが無効かエラー: {token_data}"
                        }
                    }
                # DBに refresh_token を保存
                user_obj.google_refresh_token = token_data.get("refresh_token")
                db.session.commit()

                # 予定がキャッシュされていれば自動的に登録
                cache_key = f"{user_id}_{channel_id}"
                schedule_info = schedule_cache.get(cache_key)
                if schedule_info:
                    # 登録
                    reg_ok, reg_result = create_calendar_event(user_obj.id, schedule_info)
                    if reg_ok:
                        # 成功
                        slack_client.chat_postEphemeral(
                            channel=channel_id,
                            user=user_id,
                            text="✅ Google認証が完了し、予定を登録しました！\n"
                                 f"<{reg_result}|Googleカレンダーで確認>"
                        )
                        del schedule_cache[cache_key]
                    else:
                        slack_client.chat_postEphemeral(
                            channel=channel_id,
                            user=user_id,
                            text=f"Google認証は完了しましたが予定登録に失敗: {reg_result}"
                        )
                else:
                    # 予定がキャッシュされていない
                    slack_client.chat_postEphemeral(
                        channel=channel_id,
                        user=user_id,
                        text="Google認証が完了しました！もう一度 `/plan` してください。"
                    )

                # モーダルを閉じる
                return {"response_action": "clear"}
            
            # ほかの callback_id
            return {"response_action": "clear"}

        # 2) block_actions (ボタンなど)
        elif payload.get("type") == "block_actions":
            # action_id によって分岐
            user_link = UserPlatformLink.query.filter_by(
                platform_name='slack',
                platform_user_id=user_id
            ).first()
            if not user_link:
                slack_client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="ユーザー情報が見つかりませんでした。",
                )
                return
            user_obj = user_link.user

            if action_id == "open_auth_code_modal":
                # 認証コード入力用モーダルを開く
                trigger_id = payload.get("trigger_id")
                if not trigger_id:
                    slack_client.chat_postEphemeral(
                        channel=channel_id,
                        user=user_id,
                        text="trigger_idがありません。"
                    )
                    return

                # ここで private_metadata に channel_id を入れる
                meta_dict = {"channel_id": channel_id} if channel_id else {}
                view = {
                    "type": "modal",
                    "callback_id": "auth_code_modal_submit",
                    "private_metadata": json.dumps(meta_dict),
                    "title": {
                        "type": "plain_text",
                        "text": "Google認証コード入力"
                    },
                    "submit": {
                        "type": "plain_text",
                        "text": "連携"
                    },
                    "close": {
                        "type": "plain_text",
                        "text": "キャンセル"
                    },
                    "blocks": [
                        {
                            "type": "input",
                            "block_id": "auth_code_input_block",
                            "element": {
                                "type": "plain_text_input",
                                "action_id": "auth_code_input",
                                "placeholder": {
                                    "type": "plain_text",
                                    "text": "GoogleのOAuthページで表示されたコード"
                                }
                            },
                            "label": {
                                "type": "plain_text",
                                "text": "認証コード"
                            }
                        }
                    ]
                }

                slack_client.views_open(trigger_id=trigger_id, view=view)
                return

            elif action_id == "confirm_schedule":
                # 予定をカレンダーに登録
                cache_key = f"{user_id}_{channel_id}"
                schedule_info = schedule_cache.get(cache_key)
                if not schedule_info:
                    slack_client.chat_postEphemeral(
                        channel=channel_id,
                        user=user_id,
                        text="登録する予定が見つかりません。",
                    )
                    return

                # ダブルブッキングなどの処理が必要ならここで check_schedule_conflicts(...) して分岐

                reg_ok, reg_result = create_calendar_event(user_obj.id, schedule_info)
                if reg_ok:
                    slack_client.chat_postEphemeral(
                        channel=channel_id,
                        user=user_id,
                        text=f"✅ 予定を登録しました！\n<{reg_result}|Googleカレンダーで確認>"
                    )
                    del schedule_cache[cache_key]
                else:
                    slack_client.chat_postEphemeral(
                        channel=channel_id,
                        user=user_id,
                        text=f"❌ 予定の登録に失敗: {reg_result}"
                    )
                return

            elif action_id == "cancel_schedule":
                # 予定登録をキャンセル
                cache_key = f"{user_id}_{channel_id}"
                if cache_key in schedule_cache:
                    del schedule_cache[cache_key]
                slack_client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="予定の登録をキャンセルしました。"
                )
                return

            # 他のボタン (force_schedule / use_alternative_time など) があれば適宜ここに追加

    except Exception as e:
        print(f"Error in process_interactive_action: {e}")
        # 例外が発生した場合でもSlackにメッセージを送っておく
        if channel_id:
            slack_client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"エラーが発生しました: {str(e)}"
            )

def format_datetime(dt):
    """日時 (JST)"""
    import pytz
    jst = pytz.timezone('Asia/Tokyo')
    if dt.tzinfo is None:
        dt = jst.localize(dt)
    else:
        dt = dt.astimezone(jst)
    weekdays = ["月", "火", "水", "木", "金", "土", "日"]
    w = weekdays[dt.weekday()]
    return f"{dt.year}年{dt.month}月{dt.day}日({w}) {dt.hour:02d}:{dt.minute:02d}"

def format_time(dt):
    """時刻のみ (JST)"""
    import pytz
    jst = pytz.timezone('Asia/Tokyo')
    if dt.tzinfo is None:
        dt = jst.localize(dt)
    else:
        dt = dt.astimezone(jst)
    return f"{dt.hour:02d}:{dt.minute:02d}"
