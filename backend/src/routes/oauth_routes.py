"""
OAuth認証ルート
Google認証のフローを提供します
"""
from flask import Blueprint, request, redirect, session, jsonify, url_for, current_app, render_template
import os
import logging
from datetime import datetime, timedelta
from src.models.user import User, UserPlatformLink
from src.utils.db import db
from src.utils.calendar_handler import (
    get_authorization_url, exchange_code_for_token
)

# ロギング設定
logger = logging.getLogger(__name__)

def register_oauth_routes(app):
    """
    OAuth関連のルートを登録する
    
    Args:
        app: Flaskアプリケーションインスタンス
    """
    oauth_bp = Blueprint('oauth', __name__, url_prefix='/oauth')
    
    @oauth_bp.route('/google/authorize')
    def google_authorize():
        """
        Google OAuth認証フローを開始する
        """
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
        
        logger.info(f"Google OAuth開始: user_id={user_id}, platform={platform_name}")
        
        # 認証ページにリダイレクト
        return redirect(auth_url)
    
    @oauth_bp.route('/google/callback')
    def google_callback():
        """
        Google認証コールバック処理
        認証コードを受け取り、トークンと交換する
        """
        # URLからコードを取得
        code = request.args.get('code')
        state = request.args.get('state')
        error = request.args.get('error')
        
        # エラーがある場合はエラーページにリダイレクト
        if error:
            logger.error(f"OAuth認証エラー: {error}")
            return redirect(url_for('auth_error', error=error))
        
        # 必須パラメータの確認
        if not code:
            logger.error("OAuth認証コードがありません")
            return redirect(url_for('auth_error', error="認証コードがありません"))
        
        # stateの検証
        expected_state = session.get('oauth_state')
        if state != expected_state:
            logger.error(f"State不一致: 期待={expected_state}, 実際={state}")
            return redirect(url_for('auth_error', error="セキュリティチェックに失敗しました"))
        
        try:
            # 認証コードをトークンと交換
            success, token_data = exchange_code_for_token(code)
            
            if not success:
                logger.error(f"トークン交換失敗: {token_data}")
                return redirect(url_for('auth_error', error=f"トークン取得に失敗: {token_data}"))
            
            # ユーザー情報を取得
            user_id = session.get('oauth_user_id')
            platform = session.get('oauth_platform')
            platform_user_id = session.get('oauth_platform_user_id')
            
            # ユーザーIDがない場合（セッションから取得できない場合）
            if not user_id and platform and platform_user_id:
                # プラットフォームリンクからユーザーを探す
                user_link = UserPlatformLink.query.filter_by(
                    platform_name=platform,
                    platform_user_id=platform_user_id
                ).first()
                
                if user_link:
                    user_id = user_link.user_id
                    logger.info(f"プラットフォームリンクからユーザーID {user_id} を取得")
            
            # それでもユーザーIDがない場合は新規作成
            if not user_id:
                logger.info("OAuth認証からの新規ユーザー作成")
                # 新規ユーザーを作成
                user = User(
                    display_name="Google User",
                    google_refresh_token=token_data.get("refresh_token"),
                    google_access_token=token_data.get("access_token"),
                    google_token_expiry=datetime.utcnow() + timedelta(seconds=token_data.get("expires_in", 3600))
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
                logger.info(f"新規ユーザー作成完了: ID={user_id}")
            else:
                # 既存ユーザーの場合、トークン情報を更新
                user = User.query.get(user_id)
                if user:
                    user.google_refresh_token = token_data.get("refresh_token", user.google_refresh_token)
                    user.google_access_token = token_data.get("access_token")
                    user.google_token_expiry = datetime.utcnow() + timedelta(seconds=token_data.get("expires_in", 3600))
                    db.session.commit()
                    logger.info(f"ユーザー {user_id} のトークンを更新")
            
            # セッションをクリア
            session.pop('oauth_user_id', None)
            session.pop('oauth_state', None)
            session.pop('oauth_platform', None)
            session.pop('oauth_platform_user_id', None)
            
            # 成功ページにリダイレクト
            return redirect(url_for('auth_success'))
            
        except Exception as e:
            logger.error(f"OAuth認証コールバックエラー: {e}", exc_info=True)
            return redirect(url_for('auth_error', error=str(e)))
    
    @oauth_bp.route('/google/token', methods=['POST'])
    def google_token():
        """
        認証コードをトークンと交換するAPIエンドポイント
        主にSPAからの呼び出し用
        """
        data = request.json
        code = data.get('code')
        
        if not code:
            return jsonify({"error": "認証コードが提供されていません"}), 400
        
        try:
            # 認証コードをトークンと交換
            success, token_data = exchange_code_for_token(code)
            
            if not success:
                return jsonify({"error": f"トークン取得に失敗: {token_data}"}), 400
            
            # ユーザー情報を取得
            user_id = data.get('user_id')
            platform = data.get('platform')
            platform_user_id = data.get('platform_user_id')
            
            # ユーザー情報がない場合は新規作成
            if not user_id:
                # 新規ユーザーを作成
                user = User(
                    display_name="Google User",
                    google_refresh_token=token_data.get("refresh_token"),
                    google_access_token=token_data.get("access_token"),
                    google_token_expiry=datetime.utcnow() + timedelta(seconds=token_data.get("expires_in", 3600))
                )
                db.session.add(user)
                db.session.flush()
                
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
                    if token_data.get("refresh_token"):  # refresh_tokenがない場合もある
                        user.google_refresh_token = token_data["refresh_token"]
                    user.google_access_token = token_data["access_token"]
                    user.google_token_expiry = datetime.utcnow() + timedelta(seconds=token_data.get("expires_in", 3600))
                    db.session.commit()
            
            return jsonify({
                "success": True, 
                "message": "認証に成功しました", 
                "user_id": user_id
            })
            
        except Exception as e:
            logger.error(f"トークン交換APIエラー: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 400
    
    # Blueprintをアプリケーションに登録
    app.register_blueprint(oauth_bp)
    
    # 認証成功ページテンプレート
    @app.route('/auth_success')
    def auth_success():
        """認証成功ページ"""
        return render_template('auth_success.html')
    
    # 認証エラーページテンプレート
    @app.route('/auth_error')
    def auth_error():
        """認証エラーページ"""
        error = request.args.get('error', '不明なエラー')
        return render_template('auth_error.html', error=error)