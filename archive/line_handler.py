from flask import Blueprint, request, abort, jsonify, redirect, url_for
import os
import json
import time
from datetime import datetime, timedelta
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    SourceUser, SourceGroup, SourceRoom,
    TemplateSendMessage, ButtonsTemplate, URIAction,
    QuickReply, QuickReplyButton, MessageAction
)

from archive.models import User, UserPlatformLink, db
from utils.nlp_parser import parse_schedule_from_text
from utils.calendar_handler import create_calendar_event, check_schedule_conflicts, get_authorization_url

# LINE API用の環境変数
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
FRONTEND_URL = os.getenv("FRONTEND_URL")

# メッセージ履歴の取得数上限
MESSAGE_HISTORY_LIMIT = 10

# Blueprintを作成
line_bp = Blueprint('line', __name__, url_prefix='/webhook/line')

# LINE APIクライアントとWebhookハンドラ
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

@line_bp.route('', methods=['POST'])
def callback():
    """LINEからのWebhookイベントを処理する"""
    # リクエストヘッダーからX-Line-Signatureを取得
    signature = request.headers.get('X-Line-Signature', '')
    
    # リクエストボディを取得
    body = request.get_data(as_text=True)
    
    # デバッグ用にリクエスト内容をログに出力
    print(f"Received LINE event: {body}")
    
    try:
        # 署名を検証してイベントを処理
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    
    return 'OK'

def get_message_history(source_type, source_id, limit=MESSAGE_HISTORY_LIMIT):
    """
    LINEのメッセージ履歴を取得する
    
    Args:
        source_type (str): 'user', 'group', 'room'のいずれか
        source_id (str): ユーザーID、グループID、ルームID
        limit (int): 取得するメッセージ数の上限
        
    Returns:
        list: メッセージ履歴のリスト (新しい順)
    """
    try:
        # LINE APIでメッセージ履歴を取得する
        # 注意: LINE Messaging APIでは過去のメッセージを取得するAPIが限定的
        # 実際の実装では、自前でメッセージをデータベースに保存する必要がある場合が多い
        
        # 以下はデモ実装として、実際のAPIコールの代わりに空のリストを返す
        # 本番実装では、メッセージ履歴を保存する独自のDBテーブルが必要
        messages = []
        
        # LINE Official Account APIを使用した場合の例（制限あり）:
        # if source_type == 'user':
        #     messages = line_bot_api.get_message_content(source_id)
        # ...など
        
        return messages
    except LineBotApiError as e:
        print(f"Error getting message history: {e}")
        return []
    
def combine_messages_for_analysis(messages, latest_message):
    """
    会話履歴を解析用のテキストに結合する
    
    Args:
        messages (list): 過去のメッセージリスト
        latest_message (str): 最新のメッセージ
        
    Returns:
        str: 解析用に結合されたテキスト
    """
    # 会話の流れを保持するため、古い順に並べる
    chronological_messages = list(reversed(messages))
    
    # 最新のメッセージを末尾に追加
    if latest_message not in chronological_messages:
        chronological_messages.append(latest_message)
    
    # メッセージを結合して一つのテキストにする
    # 話者の区別も入れると解析精度が向上する可能性がある
    combined_text = " ".join(chronological_messages)
    
    return combined_text

@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    """テキストメッセージイベントを処理する"""
    # メッセージ送信者情報の取得
    if isinstance(event.source, SourceUser):
        line_user_id = event.source.user_id
        try:
            profile = line_bot_api.get_profile(line_user_id)
            sender_name = profile.display_name
        except:
            sender_name = "Unknown User"
        sender_type = 'user'
        source_id = line_user_id
    elif isinstance(event.source, SourceGroup):
        line_user_id = event.source.user_id if hasattr(event.source, 'user_id') else None
        group_id = event.source.group_id
        sender_name = 'Group User'
        sender_type = 'group'
        source_id = group_id
    elif isinstance(event.source, SourceRoom):
        line_user_id = event.source.user_id if hasattr(event.source, 'user_id') else None
        room_id = event.source.room_id
        sender_name = 'Room User'
        sender_type = 'room'
        source_id = room_id
    
    # 受信したメッセージのテキスト
    text = event.message.text
    
    # ユーザー情報の取得・作成
    user_link = None
    if line_user_id:
        user_link = UserPlatformLink.query.filter_by(
            platform_name='line',
            platform_user_id=line_user_id
        ).first()
    
    if user_link:
        # 既存ユーザー
        user = user_link.user
    else:
        # 新規ユーザー作成（個人チャットの場合）
        if sender_type == 'user':
            user = User(display_name=sender_name)
            db.session.add(user)
            db.session.flush()  # IDを生成するためにフラッシュ
            
            # プラットフォームリンクを作成
            user_link = UserPlatformLink(
                user_id=user.id,
                platform_name='line',
                platform_user_id=line_user_id
            )
            db.session.add(user_link)
            db.session.commit()
        else:
            # グループ/ルームの場合はユーザーIDが存在する場合のみリンク
            if line_user_id:
                # 既存ユーザーを探す
                user_link = UserPlatformLink.query.filter_by(
                    platform_name='line',
                    platform_user_id=line_user_id
                ).first()
                
                if user_link:
                    user = user_link.user
                else:
                    # 新規ユーザー作成
                    user = User(display_name=sender_name)
                    db.session.add(user)
                    db.session.flush()
                    
                    user_link = UserPlatformLink(
                        user_id=user.id,
                        platform_name='line',
                        platform_user_id=line_user_id
                    )
                    db.session.add(user_link)
                    db.session.commit()
            else:
                # ユーザーIDが不明の場合は仮ユーザーを作成
                user = User(display_name=f"{sender_type}_{source_id[:8]}")
                db.session.add(user)
                db.session.commit()
    
    # 予定解析のトリガーとなるコマンド
    is_plan_command = text == '/plan' or text == '予定'
    is_plan_with_text = text.startswith('/plan ') or text.startswith('予定 ')
    
    if is_plan_command or is_plan_with_text:
        # Google Calendar連携確認
        if not user or not user.google_refresh_token:
            # 未連携の場合、認証リンクを送信
            auth_url, state = get_authorization_url()
            
            buttons_template = ButtonsTemplate(
                title='Google Calendar連携',
                text='予定を自動登録するには、Googleアカウントとの連携が必要です。',
                actions=[
                    URIAction(
                        label='連携する',
                        uri=auth_url
                    )
                ]
            )
            
            line_bot_api.reply_message(
                event.reply_token,
                TemplateSendMessage(
                    alt_text='Google Calendar連携',
                    template=buttons_template
                )
            )
            return
        
        # コマンドのみか、テキスト付きかで処理を分ける
        schedule_text = ''
        if is_plan_with_text:
            # `/plan` または「予定」の後のテキストを解析
            schedule_text = text[text.find(' ')+1:]
        else:
            # コマンドのみの場合、グループチャットなら最近の会話をリマインド
            if sender_type in ['group', 'room']:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="予定を追加するには、\n「予定 明日15時から1時間、オフィスで会議」\nのように具体的に入力してください。")
                )
                return
            else:
                # 個人チャットでコマンドのみの場合も同様のガイダンス
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="予定を追加するには、\n「予定 明日15時から1時間、オフィスで会議」\nのように具体的に入力してください。")
                )
                return
        
        # テキスト解析を行う
        from utils.message_parser import parse_user_input_for_scheduling
        schedule_info = parse_user_input_for_scheduling(schedule_text)
        
        # 解析に成功した場合
        if schedule_info and schedule_info.get('confidence', 0) >= 0.5:
            # 予定の詳細を確認するためのメッセージを作成
            start_datetime = schedule_info['start_datetime']
            end_datetime = schedule_info['end_datetime']
            
            if schedule_info.get('is_all_day', False):
                time_info = f"{start_datetime.strftime('%Y年%m月%d日')}（終日）"
            else:
                if start_datetime.date() == end_datetime.date():
                    time_info = f"{start_datetime.strftime('%Y年%m月%d日 %H:%M')}～{end_datetime.strftime('%H:%M')}"
                else:
                    time_info = f"{start_datetime.strftime('%Y年%m月%d日 %H:%M')}～{end_datetime.strftime('%Y年%m月%d日 %H:%M')}"
            
            confirmation_text = f"以下の予定を検出しました：\n・{schedule_info['title']}\n・{time_info}"
            if schedule_info.get('location'):
                confirmation_text += f"\n・場所: {schedule_info['location']}"
            
            confirmation_text += "\n\nGoogle Calendarに登録しますか？"
            
            # QuickReplyボタンでユーザーに確認
            quick_reply = QuickReply(
                items=[
                    QuickReplyButton(
                        action=MessageAction(label="はい、登録する", text="/confirm_schedule")
                    ),
                    QuickReplyButton(
                        action=MessageAction(label="いいえ、やめる", text="/cancel_schedule")
                    )
                ]
            )
            
            # 一時的にスケジュール情報をセッションやDBに保存
            # 実装を簡略化するため、このデモではセッション変数代わりにグローバル辞書を使用
            # 実際の実装ではRedisやDBを使うべき
            global temp_schedules
            if 'temp_schedules' not in globals():
                temp_schedules = {}
            
            # ユーザーIDをキーにして予定情報を保存
            temp_schedules[line_user_id] = schedule_info
            
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    text=confirmation_text,
                    quick_reply=quick_reply
                )
            )
        else:
            # 解析に失敗した場合
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    text="予定情報を解析できませんでした。\n例: 「予定 明日15時から2時間、オフィスでミーティング」\nのように具体的に入力してください。",
                    quick_reply=QuickReply(
                        items=[
                            QuickReplyButton(
                                action=MessageAction(label="予定を手動入力", text="/manual_schedule")
                            )
                        ]
                    )
                )
            )
    
    # 予定の確認応答
    elif text == '/confirm_schedule':
        # 一時保存した予定情報を取得
        global temp_schedules
        if 'temp_schedules' not in globals():
            temp_schedules = {}
        
        schedule_info = temp_schedules.get(line_user_id)
        if not schedule_info:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="申し訳ありません。予定情報が見つかりませんでした。再度「予定」コマンドを使用してください。")
            )
            return
        
        # ダブルブッキングチェック
        has_conflict, conflicts = check_schedule_conflicts(
            user.id,
            schedule_info['start_datetime'],
            schedule_info['end_datetime']
        )
        
        if isinstance(has_conflict, bool) and has_conflict:
            # 重複がある場合
            conflict_info = "\n".join([
                f"・{event['summary']} ({event['start']}～)"
                for event in conflicts[:3]  # 最大3件表示
            ])
            
            response_text = f"同じ時間帯に予定が重複しています：\n{conflict_info}\n\nそれでも登録しますか？"
            
            # QuickReplyで確認
            quick_reply = QuickReply(
                items=[
                    QuickReplyButton(
                        action=MessageAction(label="はい、登録する", text="/force_schedule")
                    ),
                    QuickReplyButton(
                        action=MessageAction(label="いいえ、やめる", text="/cancel_schedule")
                    )
                ]
            )
            
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=response_text, quick_reply=quick_reply)
            )
        else:
            # 重複がない場合、予定を登録
            success, result = create_calendar_event(user.id, schedule_info)
            
            if success:
                # 一時保存を削除
                if line_user_id in temp_schedules:
                    del temp_schedules[line_user_id]
                
                # 開始・終了日時のフォーマット
                start_datetime = schedule_info['start_datetime']
                end_datetime = schedule_info['end_datetime']
                
                if schedule_info.get('is_all_day', False):
                    time_info = f"{start_datetime.strftime('%Y年%m月%d日')}（終日）"
                else:
                    if start_datetime.date() == end_datetime.date():
                        time_info = f"{start_datetime.strftime('%Y年%m月%d日 %H:%M')}～{end_datetime.strftime('%H:%M')}"
                    else:
                        time_info = f"{start_datetime.strftime('%Y年%m月%d日 %H:%M')}～{end_datetime.strftime('%Y年%m月%d日 %H:%M')}"
                
                response_text = f"以下の予定を登録しました：\n・{schedule_info['title']}\n・{time_info}"
                if schedule_info.get('location'):
                    response_text += f"\n・場所: {schedule_info['location']}"
                
                response_text += f"\n\nGoogleカレンダーで確認: {result}"
            else:
                response_text = f"予定の登録に失敗しました: {result}"
            
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=response_text)
            )