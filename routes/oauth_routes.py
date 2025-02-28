from flask import Blueprint, request, redirect, session, jsonify
import os
from datetime import datetime, timedelta
from models import User, UserPlatformLink, db
from utils.calendar_handler import exchange_code_for_token, get_authorization_url

# OAuth用のBlueprintを作成
oauth_bp = Blueprint('oauth', __name__, url_prefix='/oauth')

# フロントエンドのURL
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5001")

@oauth_bp.route('/google/init')
def google_init():
    """Google OAuth認証フローを開始する"""
    # ユーザー情報の取得
    user_id = request.args.get('user_id')
    platform_name = request.args.get('platform', 'slack')
    platform_user_id = request.args.get('platform_user_id', '')
    
    # ユーザー情報をセッションに保存
    if user_id:
        session['oauth_user_id'] = user_id
    
    session['oauth_platform'] = platform_name
    session['oauth_platform_user_id'] = platform_user_id
    
    # 認証URLを生成
    auth_url, state = get_authorization_url()
    
    # stateをセッションに保存
    session['oauth_state'] = state
    
    # 認証ページのURLをクライアントに返す
    return jsonify({
        "auth_url": auth_url,
        "instructions": "以下のURLを開き、表示されたコードを入力してください。"
    })

@oauth_bp.route('/google/callback', methods=['POST'])
def google_callback():
    """認証コードを受け取り、トークンと交換する"""
    # リクエストボディからコードを取得
    data = request.json
    code = data.get('code')
    
    if not code:
        return jsonify({"error": "認証コードが提供されていません"}), 400
    
    try:
        # 認証コードをトークンと交換
        credentials = exchange_code_for_token(code)
        
        # ユーザー情報を取得
        user_id = session.get('oauth_user_id')
        platform = session.get('oauth_platform')
        platform_user_id = session.get('oauth_platform_user_id')
        
        # ユーザーIDがない場合は新規作成
        if not user_id:
            # 新規ユーザーを作成
            user = User(
                display_name="Google User",
                google_refresh_token=credentials.refresh_token,
                google_access_token=credentials.token,
                google_token_expiry=datetime.utcnow() + timedelta(seconds=credentials.expires_in)
            )
            db.session.add(user)
            db.session.flush()  # IDを取得するためにフラッシュ
            
            # プラットフォームとのリンクを作成（もしplatform情報があれば）
            if platform and platform_user_id:
                user_link = UserPlatformLink(
                    user_id=user.id,
                    platform_name=platform,
                    platform_user_id=platform_user_id
                )
                db.session.add(user_link)
            
            db.session.commit()
            user_id = user.id
        else:
            # 既存ユーザーの場合、トークン情報を更新
            user = User.query.get(user_id)
            if user:
                user.google_refresh_token = credentials.refresh_token
                user.google_access_token = credentials.token
                user.google_token_expiry = datetime.utcnow() + timedelta(seconds=credentials.expires_in)
                db.session.commit()
        
        # セッションをクリア
        session.pop('oauth_user_id', None)
        session.pop('oauth_state', None)
        session.pop('oauth_platform', None)
        session.pop('oauth_platform_user_id', None)
        
        return jsonify({"message": "認証に成功しました", "user_id": user_id})
    
    except Exception as e:
        return jsonify({"error": str(e)}), 400

# Blueprintをアプリケーションに登録するためのヘルパー関数
def register_oauth_routes(app):
    app.register_blueprint(oauth_bp)