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

from models import User, UserPlatformLink, db
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
        line_user_id = event.source.group_id
        sender_name = 'Group'
        sender_type = 'group'
        source_id = event.source.group_id
    elif isinstance(event.source, SourceRoom):
        line_user_id = event.source.room_id
        sender_name = 'Room'
        sender_type = 'room'
        source_id = event.source.room_id
    
    # 受信したメッセージのテキスト
    text = event.message.text
    
    # ユーザー情報の取得・作成
    user_link = UserPlatformLink.query.filter_by(
        platform_name='line',
        platform_user_id=line_user_id
    ).first()
    
    if user_link:
        # 既存ユーザー
        user = user_link.user
    else:
        # 新規ユーザー作成
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
            # グループやルームの場合も仮ユーザーを作成
            user = User(display_name=f"{sender_type}_{source_id[:8]}")
            db.session.add(user)
            db.session.flush()
            
            user_link = UserPlatformLink(
                user_id=user.id,
                platform_name='line',
                platform_user_id=source_id
            )
            db.session.add(user_link)
            db.session.commit()
    
    # /plan コマンドの処理
    if text == '/plan' or text == '予定':
        # Google Calendar連携確認
        if not user.google_refresh_token:
            # 未連携の場合、認証リンクを送信
            auth_url, state = get_authorization_url()
            # stateをセッションに保存するなどの処理が必要
            
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
        
        # メッセージ履歴を取得
        message_history = get_message_history(sender_type, source_id)
        
        # 履歴が取得できない場合の処理
        if not message_history:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="最近の会話から予定を検出できませんでした。\n「予定」コマンドの後に具体的な予定内容を入力するか、「予定 明日の15時から会議」のように詳細を含めてください。")
            )
            return
        
        # メッセージ履歴を結合して解析用のテキストを作成
        combined_text = combine_messages_for_analysis(message_history, text)
        
        # NLPで予定情報を解析
        schedule_info = parse_schedule_from_text(combined_text)
        
        # 解析に成功した場合
        if schedule_info['confidence'] >= 0.5 and schedule_info['start_datetime']:
            # 予定の詳細を確認するためのメッセージ
            confirmation_text = f"以下の予定を検出しました：\n・{schedule_info['title']}\n・{schedule_info['start_datetime'].strftime('%Y年%m月%d日 %H:%M')}～{schedule_info['end_datetime'].strftime('%H:%M')}"
            if schedule_info['location']:
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
            # 実際の実装ではDBに一時保存するか、セッション管理する必要がある
            # この例では省略
            
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
                    text="最近の会話から予定情報を検出できませんでした。\n例：「明日の15時からオフィスでミーティング」のような会話が必要です。\n\n具体的に「予定 明日15時から1時間会議」のように入力することもできます。",
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
        # 実際の実装では、一時保存した予定情報を取得する処理が必要
        # この例では仮の実装として固定の予定を使用
        
        # ダミーのスケジュール情報
        import pytz
        from datetime import datetime, timedelta
        
        dummy_start = datetime.now(pytz.timezone('Asia/Tokyo')) + timedelta(days=1)
        dummy_start = dummy_start.replace(hour=15, minute=0, second=0, microsecond=0)
        dummy_end = dummy_start + timedelta(hours=1)
        
        schedule_info = {
            'title': '予定確認テスト',
            'start_datetime': dummy_start,
            'end_datetime': dummy_end,
            'location': 'オフィス',
            'description': '会話履歴から検出された予定',
        }
        
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
                response_text = f"以下の予定を登録しました：\n・{schedule_info['title']}\n・{schedule_info['start_datetime'].strftime('%Y年%m月%d日 %H:%M')}～{schedule_info['end_datetime'].strftime('%H:%M')}"
                if schedule_info['location']:
                    response_text += f"\n・場所: {schedule_info['location']}"
                
                response_text += f"\n\nGoogleカレンダーで確認: {result}"
            else:
                response_text = f"予定の登録に失敗しました: {result}"
            
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=response_text)
            )
    
    # 予定の強制登録
    elif text == '/force_schedule':
        # 実際の実装では、一時保存した予定情報を取得する処理が必要
        # この例では仮の実装として固定の予定を使用
        
        # ダミースケジュール（本来はDBやセッションから取得）
        import pytz
        from datetime import datetime, timedelta
        
        dummy_start = datetime.now(pytz.timezone('Asia/Tokyo')) + timedelta(days=1)
        dummy_start = dummy_start.replace(hour=15, minute=0, second=0, microsecond=0)
        dummy_end = dummy_start + timedelta(hours=1)
        
        schedule_info = {
            'title': '予定確認テスト(重複あり)',
            'start_datetime': dummy_start,
            'end_datetime': dummy_end,
            'location': 'オフィス',
            'description': '会話履歴から検出された予定（重複を承知で登録）',
        }
        
        # 予定を登録
        success, result = create_calendar_event(user.id, schedule_info)
        
        if success:
            response_text = f"重複を承知で以下の予定を登録しました：\n・{schedule_info['title']}\n・{schedule_info['start_datetime'].strftime('%Y年%m月%d日 %H:%M')}～{schedule_info['end_datetime'].strftime('%H:%M')}"
            if schedule_info['location']:
                response_text += f"\n・場所: {schedule_info['location']}"
            
            response_text += f"\n\nGoogleカレンダーで確認: {result}"
        else:
            response_text = f"予定の登録に失敗しました: {result}"
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=response_text)
        )
    
    # 予定のキャンセル
    elif text == '/cancel_schedule':
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="予定の登録をキャンセルしました。")
        )
    
    # 手動予定入力
    elif text == '/manual_schedule':
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="「予定」に続けて、具体的な内容を入力してください。\n例: 「予定 明日15時から2時間、オフィスでミーティング」")
        )
    
    # 予定で始まるメッセージを処理（通常の予定入力）
    elif text.startswith('/plan ') or text.startswith('予定 '):
        # 「/plan」または「予定」の後のテキストを解析
        schedule_text = text[text.find(' ')+1:]
        
        # Google Calendar連携確認
        if not user.google_refresh_token:
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
        
        # NLPで予定情報を解析
        schedule_info = parse_schedule_from_text(schedule_text)
        
        # 解析に成功した場合
        if schedule_info['confidence'] >= 0.5 and schedule_info['start_datetime']:
            # 予定の詳細を確認するためのメッセージ
            confirmation_text = f"以下の予定を検出しました：\n・{schedule_info['title']}\n・{schedule_info['start_datetime'].strftime('%Y年%m月%d日 %H:%M')}～{schedule_info['end_datetime'].strftime('%H:%M')}"
            if schedule_info['location']:
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
            # 実際の実装ではDBに一時保存するか、セッション管理する必要がある
            # この例では省略
            
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
                TextSendMessage(text="予定の詳細を解析できませんでした。\n例：「予定 明日の15時から2時間、オフィスでミーティング」のように入力してください。")
            )
    elif text.startswith('/help') or text == 'ヘルプ':
        # ヘルプメッセージ
        help_text = """【使い方】
1. 「/plan」と入力すると、最近の会話から予定を自動検出します。

2. 「予定 明日15時から1時間、プロジェクト会議」のように具体的に入力することもできます。

3. 初回利用時はGoogleアカウントとの連携が必要です。

4. 「/status」で連携状況を確認できます。

5. 「/help」でこのヘルプを表示します。"""
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=help_text)
        )
    elif text.startswith('/status'):
        # 連携状況確認
        status_text = f"【連携状況】\nLINE: 連携済み (ID: {line_user_id[:5]}...)\n"
        
        if user.google_refresh_token:
            status_text += "Google Calendar: 連携済み✓"
        else:
            status_text += "Google Calendar: 未連携"
            
            # 連携リンクも表示
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
                [
                    TextSendMessage(text=status_text),
                    TemplateSendMessage(
                        alt_text='Google Calendar連携',
                        template=buttons_template
                    )
                ]
            )
            return
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=status_text)
        )
    else:
        # 会話の内容を保存する処理
        # 実際の実装ではDBに会話履歴を保存する処理が必要
        # この例では省略（LINE Messaging APIでは会話履歴の自動保存はできない）
        
        # 一般的なメッセージには反応しない（会話の自然な流れを妨げないため）
        pass

# Google OAuth認証コールバック用のルート
@line_bp.route('/oauth/callback')
def oauth_callback():
    """Google OAuth認証コールバックを処理する"""
    code = request.args.get('code')
    state = request.args.get('state')
    error = request.args.get('error')
    
    if error:
        return f"認証エラー: {error}", 400
    
    if not code:
        return "認証コードがありません", 400
    
    # このstateをもとにユーザーを特定する処理が必要
    # 簡易実装では省略
    
    # トークンを取得・保存する処理
    # ここでは省略
    
    return redirect(f"{FRONTEND_URL}/auth_success.html")

# Blueprintをアプリケーションに登録するためのヘルパー関数
def register_line_handler(app):
    app.register_blueprint(line_bp)