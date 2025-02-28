#!/usr/bin/env python3
"""
スマート予定管理バックエンドサーバー
Slackからの予定検出とGoogleカレンダー連携を処理します
"""
from flask import Flask, render_template, request, jsonify, Response
import os
import redis
import json
import uuid
import logging
from datetime import datetime, timedelta

from src.config import Config
from src.utils.db import db
from src.models.user import User, UserPlatformLink
from src.handlers.slack_handler import slack_bp
from src.routes.oauth_routes import register_oauth_routes
from src.utils.calendar_handler import create_calendar_event, check_schedule_conflicts

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('app.log')
    ]
)
logger = logging.getLogger(__name__)

def create_app(test_config=None):
    """
    Flaskアプリケーションを作成
    
    Args:
        test_config: テスト用の設定（オプション）
        
    Returns:
        Flask application instance
    """
    # Flaskアプリケーションの初期化
    app = Flask(__name__, 
                static_folder='static',
                template_folder='templates')
    
    # 設定の適用
    if test_config is None:
        app.config.from_object(Config)
    else:
        app.config.update(test_config)
    
    # 静的ファイル用のディレクトリを作成
    os.makedirs(os.path.join(app.root_path, 'static'), exist_ok=True)

    # データベースの初期化
    db.init_app(app)
    
    # テーブル作成
    with app.app_context():
        db.create_all()
    
    # Redisクライアントの設定と初期化
    redis_url = app.config.get('REDIS_URL', 'redis://localhost:6379/0')
    notification_channel = app.config.get('NOTIFICATION_CHANNEL', 'smart_scheduler_notifications')
    
    try:
        redis_client = redis.from_url(redis_url)
        app.redis_client = redis_client
        app.notification_channel = notification_channel
        logger.info(f"Redisクライアント初期化成功: {redis_url}")
    except Exception as e:
        logger.error(f"Redisクライアント初期化エラー: {e}", exc_info=True)
        app.redis_client = None
        app.notification_channel = None
    
    # ルートの登録
    app.register_blueprint(slack_bp)
    register_oauth_routes(app)

    # ホームページルート
    @app.route('/')
    def index():
        """APIサーバーのホームページ"""
        return render_template('index.html')

    # API健全性チェック
    @app.route('/api/health')
    def health_check():
        """API健全性チェックエンドポイント"""
        return jsonify({
            "status": "healthy",
            "version": "1.0.0",
            "timestamp": datetime.now().isoformat()
        })

    # イベント通知処理API
    @app.route("/api/events", methods=["POST"])
    def create_event():
        """
        イベント作成エンドポイント
        フロントエンドや他のシステムからも利用可能
        """
        data = request.get_json()
        if data is None:
            logger.error("イベント作成リクエストにデータがありません")
            return jsonify({"error": "No data provided"}), 400
        
        # イベントIDを生成
        event_id = data.get('event_id', str(uuid.uuid4()))
        if 'event_id' not in data:
            data['event_id'] = event_id
        
        # イベントデータをRedisに保存 (5分間有効)
        if app.redis_client:
            try:
                app.redis_client.setex(f"event:{event_id}", 300, json.dumps(data))
                
                # 通知をRedisパブサブチャネルに送信
                app.redis_client.publish(app.notification_channel, json.dumps({
                    'type': 'event',
                    'event_id': event_id,
                    'summary': data.get('summary', '予定'),
                    'date': data.get('date', ''),
                    'time': data.get('time', ''),
                    'location': data.get('location', ''),
                    'description': data.get('description', '')
                }))
                
                logger.info(f"イベント作成: id={event_id}")
            except Exception as e:
                logger.error(f"Redis通知送信エラー: {e}", exc_info=True)
        
        return jsonify({"status": "ok", "event_id": event_id}), 201

    @app.route("/api/events/<event_id>", methods=["GET"])
    def get_event(event_id):
        """イベント情報取得エンドポイント"""
        if not app.redis_client:
            logger.error("Redisクライアントが初期化されていません")
            return jsonify({"error": "Redis client not available"}), 500
        
        event_data = app.redis_client.get(f"event:{event_id}")
        if not event_data:
            logger.warning(f"イベントが見つかりません: {event_id}")
            return jsonify({"error": "Event not found"}), 404
        
        return Response(event_data, mimetype='application/json')
    
    @app.route("/api/events/<event_id>/approve", methods=["POST"])
    def approve_event(event_id):
        """イベント承認処理エンドポイント"""
        # Redisからイベント情報を取得
        if not app.redis_client:
            logger.error("Redisクライアントが初期化されていません")
            return jsonify({"error": "Redis client not available"}), 500
        
        event_data_raw = app.redis_client.get(f"event:{event_id}")
        if not event_data_raw:
            logger.warning(f"イベントが見つかりません: {event_id}")
            return jsonify({"error": "Event not found"}), 404
        
        try:
            data = json.loads(event_data_raw)
            user_id = data.get("user_id")
            event_data = data.get("event_data")
            
            if not user_id or not event_data:
                logger.error(f"イベントデータ不足: user_id={user_id}, event_data={bool(event_data)}")
                return jsonify({"error": "Missing user_id or event_data"}), 400
            
            # Googleカレンダーにイベントを登録
            success, result = create_calendar_event(user_id, event_data)
            
            # 結果を通知チャネルにパブリッシュ
            notification = {
                'type': 'result',
                'success': success,
                'message': f"予定「{event_data.get('title', '予定')}」の登録が{'成功' if success else '失敗'}しました。"
            }
            app.redis_client.publish(app.notification_channel, json.dumps(notification))
            
            # Slack通知（オプション）
            if data.get("channel_id") and data.get("slack_user_id"):
                try:
                    from src.handlers.slack_handler import slack_client
                    if slack_client:
                        slack_client.chat_postEphemeral(
                            channel=data["channel_id"],
                            user=data["slack_user_id"],
                            text=f"✅ 予定「{event_data.get('title', '予定')}」をGoogleカレンダーに追加しました。"
                        )
                except Exception as e:
                    logger.error(f"Slack通知送信エラー: {e}", exc_info=True)
            
            # イベント情報をRedisから削除
            app.redis_client.delete(f"event:{event_id}")
            
            logger.info(f"イベント承認処理完了: id={event_id}, success={success}")
            return jsonify({
                "success": success,
                "result": result
            })
        except Exception as e:
            logger.error(f"イベント承認処理エラー: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500

    @app.route("/api/events/<event_id>/deny", methods=["POST"])
    def deny_event(event_id):
        """イベント拒否処理エンドポイント"""
        # Redisからイベント情報を取得
        if not app.redis_client:
            logger.error("Redisクライアントが初期化されていません")
            return jsonify({"error": "Redis client not available"}), 500
        
        event_data_raw = app.redis_client.get(f"event:{event_id}")
        if not event_data_raw:
            logger.warning(f"イベントが見つかりません: {event_id}")
            return jsonify({"error": "Event not found"}), 404
        
        try:
            data = json.loads(event_data_raw)
            
            # 結果を通知チャネルにパブリッシュ
            notification = {
                'type': 'result',
                'success': False,
                'message': f"予定「{data.get('summary', '予定')}」の登録がキャンセルされました。"
            }
            app.redis_client.publish(app.notification_channel, json.dumps(notification))
            
            # Slack通知（オプション）
            if data.get("channel_id") and data.get("slack_user_id"):
                try:
                    from src.handlers.slack_handler import slack_client
                    if slack_client:
                        slack_client.chat_postEphemeral(
                            channel=data["channel_id"],
                            user=data["slack_user_id"],
                            text=f"❌ 予定「{data.get('summary', '予定')}」の追加をキャンセルしました。"
                        )
                except Exception as e:
                    logger.error(f"Slack通知送信エラー: {e}", exc_info=True)
            
            # イベント情報をRedisから削除
            app.redis_client.delete(f"event:{event_id}")
            
            logger.info(f"イベント拒否処理完了: id={event_id}")
            return jsonify({"status": "ok"})
        except Exception as e:
            logger.error(f"イベント拒否処理エラー: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500
    
    # カスタムエラーハンドラー
    @app.errorhandler(404)
    def not_found(error):
        logger.warning(f"404エラー: {request.path}")
        return jsonify({"error": "Not found"}), 404
    
    @app.errorhandler(500)
    def server_error(error):
        logger.error(f"500エラー: {str(error)}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500

    return app

# Gunicorn用にappをトップレベルで定義
app = create_app()

if __name__ == "__main__":
    # ローカル開発用の起動コード
    port = int(os.getenv('PORT', 5001))
    app.run(host="0.0.0.0", port=port, debug=app.config.get("DEBUG", False))