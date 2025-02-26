from flask import Blueprint, request, jsonify
import os
import json
import hmac
import hashlib
import time
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# Slackからのリクエスト検証用シークレット (後で.envに追加します)
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "dummy_secret_for_now")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "dummy_token_for_now")

# Blueprintを作成
slack_bp = Blueprint('slack', __name__, url_prefix='/webhook/slack')
slack_client = WebClient(token=SLACK_BOT_TOKEN)

def verify_slack_request(request):
    """Slackからのリクエストを検証する"""
    request_body = request.get_data().decode('utf-8')
    timestamp = request.headers.get('X-Slack-Request-Timestamp', '')

    # タイムスタンプが5分以上前のリクエストは拒否（リプレイアタック対策）
    if not timestamp or abs(time.time() - int(timestamp)) > 60 * 5:
        return False

    sig_basestring = f"v0:{timestamp}:{request_body}".encode('utf-8')
    my_signature = 'v0=' + hmac.new(
        SLACK_SIGNING_SECRET.encode('utf-8'),
        sig_basestring,
        hashlib.sha256
    ).hexdigest()

    slack_signature = request.headers.get('X-Slack-Signature', '')
    return hmac.compare_digest(my_signature, slack_signature)

@slack_bp.route('/events', methods=['POST'])
def slack_events():
    """Slackからのイベントを処理する"""
    # 開発中は検証をスキップ
    # if not verify_slack_request(request):
    #     return jsonify({"error": "Invalid request"}), 403

    data = request.json

    # URL検証チャレンジ（Slack Bot設定時に必要）
    if data and data.get('type') == 'url_verification':
        return jsonify({"challenge": data.get('challenge')})

    # デバッグ用にリクエスト内容をログに出力
    print(f"Received Slack event: {json.dumps(data, indent=2)}")

    # イベントハンドリング（これから実装）

    return jsonify({"status": "ok"})

