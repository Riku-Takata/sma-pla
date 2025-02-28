from flask import Blueprint, request, redirect, session, url_for, jsonify
import os
from datetime import datetime, timedelta
from models import User, UserPlatformLink, db
from utils.calendar_handler import create_oauth_flow, exchange_code_for_token

# OAuth用のBlueprintを作成
oauth_bp = Blueprint('oauth', __name__, url_prefix='/oauth')

# フロントエンドのURL
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5001")

@oauth_bp.route('/google/init')
def google_init():
    """Google OAuth認証フローを開始する"""
    # ユーザーIDを取得（セッションやクエリパラメータから）
    user_id = request.args.get('user_id')
    platform_name = request.args.get('platform', 'slack')
    platform_user_id = request.args.get('platform_user_id', '')
    
    if not user_id and platform_name and platform_user_id:
        # プラットフォーム情報から関連するユーザーを検索
        user_link = UserPlatformLink.query.filter_by(
            platform_name=platform_name,
            platform_user_id=platform_user_id
        ).first()
        
        if user_link:
            user_id = user_link.user_id
    
    # ユーザーIDをセッションに保存
    if user_id:
        session['oauth_user_id'] = user_id
    
    # プラットフォーム情報をセッションに保存
    session['oauth_platform'] = platform_name
    session['oauth_platform_user_id'] = platform_user_id
    
    # Googleの認証URLを生成
    from utils.calendar_handler import get_authorization_url
    auth_url, state = get_authorization_url()
    
    # stateをセッションに保存して後で検証できるようにする
    session['oauth_state'] = state
    
    # 認証ページにリダイレクト
    return redirect(auth_url)

@oauth_bp.route('/google/callback')
def google_callback():
    """Google OAuth認証コールバックを処理する"""
    # エラー処理
    error = request.args.get('error')
    if error:
        return redirect(f"{FRONTEND_URL}/auth_error.html?error={error}")
    
    # コードとstateを取得
    code = request.args.get('code')
    state = request.args.get('state')
    
    # stateの検証（セキュリティ対策）
    if state != session.get('oauth_state'):
        return redirect(f"{FRONTEND_URL}/auth_error.html?error=invalid_state")
    
    # 認証コードをトークンと交換
    try:
        credentials = exchange_code_for_token(code)
    except Exception as e:
        return redirect(f"{FRONTEND_URL}/auth_error.html?error={str(e)}")
    
    # ユーザー情報を取得
    user_id = session.get('oauth_user_id')
    platform = session.get('oauth_platform')
    platform_user_id = session.get('oauth_platform_user_id')
    
    # ユーザーIDがない場合は新規作成
    if not user_id:
        # 新規ユーザーを作成
        user = User(
            display_name="Google User",  # OAuth後にGoogle APIから名前を取得するとベター
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
    
    # 成功ページにリダイレクト
    return redirect(f"{FRONTEND_URL}/auth_success.html")

# Blueprintをアプリケーションに登録するためのヘルパー関数
def register_oauth_routes(app):
    app.register_blueprint(oauth_bp)