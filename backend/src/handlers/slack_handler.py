"""
Slackハンドラー
Slackのコマンドを処理し、フロントエンドUIに通知する
"""
from flask import Blueprint, request, jsonify, current_app
import os
import json
import logging
import threading
import pytz
import redis
import uuid
from datetime import datetime
from slack_sdk import WebClient
from slack_sdk.signature import SignatureVerifier
from slack_sdk.errors import SlackApiError

from src.models.user import User, UserPlatformLink
from src.utils.db import db
from src.utils.nlp_parser import parse_user_input_for_scheduling

# 環境変数からSlack API情報を取得
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5001")

# 取得するメッセージ履歴の上限
MESSAGE_HISTORY_LIMIT = 10

# ロギング設定
logger = logging.getLogger(__name__)

# Blueprintの設定
slack_bp = Blueprint('slack', __name__, url_prefix='/webhook/slack')

# Slack APIクライアントとシグネチャ検証の設定
slack_client = WebClient(token=SLACK_BOT_TOKEN) if SLACK_BOT_TOKEN else None
signature_verifier = SignatureVerifier(SLACK_SIGNING_SECRET) if SLACK_SIGNING_SECRET else None

def verify_slack_request(request):
    """
    Slackからのリクエストを検証する
    
    Args:
        request: Flaskのリクエストオブジェクト
        
    Returns:
        bool: 検証に成功した場合はTrue、それ以外はFalse
    """
    if not SLACK_SIGNING_SECRET:
        logger.warning("SLACK_SIGNING_SECRET未設定。リクエスト検証をスキップします。")
        return True
    
    if not signature_verifier:
        logger.warning("SignatureVerifierが初期化されていません。")
        return True
    
    try:
        return signature_verifier.is_valid(
            request.get_data().decode('utf-8'),
            request.headers.get('X-Slack-Request-Timestamp', ''),
            request.headers.get('X-Slack-Signature', '')
        )
    except Exception as e:
        logger.error(f"Slackリクエスト検証エラー: {e}", exc_info=True)
        return False

@slack_bp.route('/events', methods=['POST'])
def slack_events():
    """
    Slackのイベント(メッセージなど)を受け取る
    
    Returns:
        JSONレスポンス
    """
    if not verify_slack_request(request):
        logger.warning("Slackリクエスト検証に失敗しました")
        return jsonify({"error": "Invalid request"}), 403

    data = request.json
    if data and data.get('type') == 'url_verification':
        # Slack App設定時のURL検証
        logger.info("Slack URL検証リクエストを受信しました")
        return jsonify({"challenge": data.get('challenge')})
    
    # Slackには即座に200 OKを返す必要がある
    return jsonify({"status": "ok"})

@slack_bp.route('/command', methods=['POST'])
def slack_commands():
    """
    Slackのスラッシュコマンド (/plan) を処理する
    
    Returns:
        JSONレスポンス
    """
    if not verify_slack_request(request):
        logger.warning("Slackリクエスト検証に失敗しました")
        return jsonify({"error": "Invalid request"}), 403

    form_data = request.form
    command = form_data.get('command')
    text = form_data.get('text', '')
    channel_id = form_data.get('channel_id')
    user_id = form_data.get('user_id')
    team_id = form_data.get('team_id')

    logger.info(f"Slackコマンド受信: {command} {text[:30]}...")

    if command == '/plan':
        app = current_app._get_current_object()

        # 非同期でコマンド処理を行う
        thread = threading.Thread(
            target=process_plan_command_with_app_context,
            args=(app, channel_id, user_id, team_id, text)
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

def process_plan_command_with_app_context(app, channel_id, user_id, team_id, text):
    """
    アプリケーションコンテキストでプランコマンドを処理する
    
    Args:
        app: Flaskアプリケーションオブジェクト
        channel_id (str): SlackチャンネルID
        user_id (str): SlackユーザーID
        team_id (str): SlackチームID
        text (str): コマンドテキスト
    """
    with app.app_context():
        process_plan_command(channel_id, user_id, team_id, text)

# Redisにイベントデータを保存する関数を改善
def save_event_to_redis(event_id, event_data):
    """
    イベントデータをRedisに保存し、通知チャネルにパブリッシュする
    
    Args:
        event_id (str): イベントID
        event_data (dict): イベントデータ
        
    Returns:
        bool: 成功時はTrue、失敗時はFalse
    """
    try:
        # アプリケーションコンテキストからRedisクライアントを取得
        redis_client = current_app.redis_client
        notification_channel = current_app.notification_channel
        
        if not redis_client or not notification_channel:
            logger.warning("Redisが利用できないため、通知を送信できません")
            return False
        
        # イベントデータをRedisに保存（5分間有効）
        redis_client.setex(f"event:{event_id}", 300, json.dumps(event_data))
        
        # 通知をRedisパブサブチャネルに送信
        notification_data = {
            'type': 'event',
            'event_id': event_id,
            'summary': event_data.get('summary', '予定'),
            'date': event_data.get('date', ''),
            'time': event_data.get('time', ''),
            'location': event_data.get('location', '')
        }
        
        redis_client.publish(notification_channel, json.dumps(notification_data))
        logger.info(f"イベント通知をRedisに送信: event_id={event_id}")
        return True
        
    except Exception as e:
        logger.error(f"Redis通知送信エラー: {e}", exc_info=True)
        return False

def process_plan_command(channel_id, user_id, team_id, text):
    """
    1) テキスト or 最近の会話から予定を解析
    2) フロントエンド通知サーバーに解析結果を送信
    
    Args:
        channel_id (str): SlackチャンネルID
        user_id (str): SlackユーザーID
        team_id (str): SlackチームID
        text (str): コマンドテキスト
    """
    if not slack_client:
        logger.error("Slack APIクライアントが初期化されていません")
        return
    
    # ユーザー取得または作成
    user_link = UserPlatformLink.query.filter_by(
        platform_name='slack',
        platform_user_id=user_id
    ).first()
    
    if user_link:
        user = user_link.user
        logger.debug(f"既存ユーザーを取得: {user.id}")
    else:
        # 新規ユーザー作成
        try:
            slack_user_info = slack_client.users_info(user=user_id)
            display_name = slack_user_info['user']['real_name']
            email = slack_user_info['user'].get('profile', {}).get('email')
            logger.info(f"Slackユーザー情報取得: {display_name}")
        except SlackApiError as e:
            logger.error(f"Slackユーザー情報取得エラー: {e}", exc_info=True)
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
        logger.info(f"新規ユーザー作成: ID={user.id}, 名前={display_name}")

    # 予定を解析
    schedule_info = None
    if text.strip():
        # コマンドテキストがある場合はそれを解析
        logger.info(f"コマンドテキストから予定を解析: {text[:50]}...")
        schedule_info = parse_user_input_for_scheduling(text)
    else:
        # テキストがない場合は会話履歴を取得して解析
        messages = get_conversation_history(channel_id, MESSAGE_HISTORY_LIMIT)
        if messages:
            logger.info(f"会話履歴から予定を解析: {len(messages)}件のメッセージ")
            combined_text = "\n".join(m.get('text', '') for m in messages if m.get('text'))
            schedule_info = parse_user_input_for_scheduling(combined_text)
            logger.debug(f"解析テキスト: {combined_text[:100]}...")

    if not schedule_info or schedule_info.get('confidence', 0) < 0.5:
        # 解析に失敗または低信頼度の場合
        logger.warning(f"予定解析失敗または低信頼度: {schedule_info.get('confidence', 0) if schedule_info else 0}")
        try:
            slack_client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="予定情報を検出できませんでした。日時や場所などを含めて入力してください。"
            )
        except SlackApiError as e:
            logger.error(f"Slackメッセージ送信エラー: {e}", exc_info=True)
        return

    # フロントエンドへの通知データを準備
    # タイムゾーンをJSTに統一
    jst = pytz.timezone('Asia/Tokyo')
    start_dt = schedule_info['start_datetime']
    end_dt = schedule_info['end_datetime']
    
    if start_dt.tzinfo is None:
        start_dt = jst.localize(start_dt)
    else:
        start_dt = start_dt.astimezone(jst)
    
    if end_dt.tzinfo is None:
        end_dt = jst.localize(end_dt)
    else:
        end_dt = end_dt.astimezone(jst)
    
    # 通知データを作成
    event_id = str(uuid.uuid4())
    event_data = {
        'event_id': event_id,
        'type': 'event',
        'source': 'slack',
        'channel_id': channel_id,
        'user_id': user.id,
        'slack_user_id': user_id,
        'slack_team_id': team_id,
        'summary': schedule_info['title'],
        'date': start_dt.strftime('%Y-%m-%d'),
        'time': start_dt.strftime('%H:%M'),
        'end_time': end_dt.strftime('%H:%M'),
        'location': schedule_info.get('location', ''),
        'description': schedule_info.get('description', ''),
        'is_all_day': schedule_info.get('is_all_day', False),
        'event_data': {
            'title': schedule_info['title'],
            'start_datetime': start_dt.isoformat(),
            'end_datetime': end_dt.isoformat(),
            'location': schedule_info.get('location', ''),
            'description': schedule_info.get('description', ''),
            'is_all_day': schedule_info.get('is_all_day', False)
        }
    }
    
    # Redisにイベントデータを保存
    if save_event_to_redis(event_id, event_data):
        logger.info(f"イベント通知をRedisに送信: event_id={event_id}")
        
        # ユーザーにフィードバックメッセージを送信
        try:
            slack_client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="✅ 予定を検出しました。通知センターで確認してください。"
            )
        except SlackApiError as e:
            logger.error(f"Slackメッセージ送信エラー: {e}", exc_info=True)
    else:
        # Redis通知が失敗した場合のフォールバック
        try:
            # 直接ユーザーに予定情報を送信
            slack_client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"📅 予定を検出しました: {schedule_info['title']} @ {start_dt.strftime('%Y-%m-%d %H:%M')}\n⚠️ 通知センターへの送信に失敗しましたが、Googleカレンダーへの登録は可能です。"
            )
        except SlackApiError as e:
            logger.error(f"Slackメッセージ送信エラー: {e}", exc_info=True)

def get_conversation_history(channel_id, limit):
    """
    Slackの会話履歴を取得する
    
    Args:
        channel_id (str): SlackチャンネルID
        limit (int): 取得上限数
        
    Returns:
        list: メッセージリスト
    """
    if not slack_client:
        logger.error("Slack APIクライアントが初期化されていません")
        return []
    
    try:
        resp = slack_client.conversations_history(channel=channel_id, limit=limit)
        logger.debug(f"会話履歴取得成功: {len(resp.get('messages', []))}件")
        return resp.get('messages', [])
    except SlackApiError as e:
        logger.error(f"会話履歴取得エラー: {e}", exc_info=True)
        return []