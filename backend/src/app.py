#!/usr/bin/env python3
"""
スマート予定管理バックエンドサーバー
Slackからの予定検出とGoogleカレンダー連携を処理します
"""
from flask import Flask, render_template, request, jsonify, Response
import os
import json
import uuid
import logging
import time
import traceback
from datetime import datetime, timedelta
import requests

# ロギング設定を改善
logging.basicConfig(
    level=logging.DEBUG,  # DEBUGレベルに変更
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('app.log')
    ]
)
logger = logging.getLogger(__name__)

# 起動時にシステム情報をログに出力
logger.info(f"Python Path: {os.environ.get('PYTHONPATH', 'Not set')}")
logger.info(f"Current Directory: {os.getcwd()}")
logger.debug(f"Directory Contents: {os.listdir('.')}")
try:
    logger.debug(f"src Directory Contents: {os.listdir('./src')}")
except Exception as e:
    logger.warning(f"Could not list src directory: {e}")

# インポートパスを改善
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

# すべてのインポートを分離して詳細なエラーログを取得
try:
    from src.config import Config
    logger.info("Successfully imported Config")
except Exception as e:
    logger.error(f"Failed to import Config: {e}")
    logger.error(traceback.format_exc())
    # シンプルな設定クラスをフォールバックとして定義
    class Config:
        DEBUG = True
        SECRET_KEY = "fallback_secret_key"
        SQLALCHEMY_DATABASE_URI = "sqlite:///fallback.db"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

try:
    from src.utils.db import db
    logger.info("Successfully imported db")
except Exception as e:
    logger.error(f"Failed to import db: {e}")
    logger.error(traceback.format_exc())
    # SQLAlchemyのインポートを試みる
    try:
        from flask_sqlalchemy import SQLAlchemy
        db = SQLAlchemy()
        logger.info("Created fallback SQLAlchemy instance")
    except Exception as e2:
        logger.error(f"Failed to create fallback db: {e2}")
        logger.error(traceback.format_exc())

try:
    from src.models.user import User, UserPlatformLink
    logger.info("Successfully imported User models")
except Exception as e:
    logger.error(f"Failed to import User models: {e}")
    logger.error(traceback.format_exc())
    # モデルがないなら後で作る

try:
    from src.handlers.slack_handler import slack_bp
    logger.info("Successfully imported slack_bp")
except Exception as e:
    logger.error(f"Failed to import slack_bp: {e}")
    logger.error(traceback.format_exc())
    # フォールバックとして空のBlueprintを作成
    try:
        from flask import Blueprint
        slack_bp = Blueprint('slack', __name__, url_prefix='/webhook/slack')
    except Exception as e2:
        logger.error(f"Failed to create fallback slack_bp: {e2}")

try:
    from src.routes.oauth_routes import register_oauth_routes
    logger.info("Successfully imported register_oauth_routes")
except Exception as e:
    logger.error(f"Failed to import register_oauth_routes: {e}")
    logger.error(traceback.format_exc())
    # シンプルな関数を定義
    def register_oauth_routes(app):
        logger.warning("Using fallback oauth routes implementation")
        @app.route("/oauth/google/callback")
        def oauth_callback():
            return jsonify({"status": "Fallback oauth implementation"})

try:
    from src.utils.calendar_handler import create_calendar_event, check_schedule_conflicts
    logger.info("Successfully imported calendar handlers")
except Exception as e:
    logger.error(f"Failed to import calendar handlers: {e}")
    logger.error(traceback.format_exc())
    # ダミー関数を定義
    def create_calendar_event(user_id, event_data):
        logger.warning("Using fallback calendar_event implementation")
        return True, "http://calendar.example.com"
    
    def check_schedule_conflicts(user_id, start_time, end_time):
        logger.warning("Using fallback check_schedule_conflicts implementation")
        return False, []

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
    try:
        db.init_app(app)
        
        # テーブル作成
        with app.app_context():
            db.create_all()
            logger.info("Database tables created successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        logger.error(traceback.format_exc())
    
    # ⚠️ Redisの初期化を完全にオプショナルにする（redis-freeモード） ⚠️
    redis_url = app.config.get('REDIS_URL', None)
    notification_channel = app.config.get('NOTIFICATION_CHANNEL', 'smart_scheduler_notifications')
    
    # Redis接続情報をログに出力 (診断用)
    logger.info(f"Redis接続URL: {redis_url}")
    
    redis_client = None
    try:
        if redis_url:
            import redis
            redis_client = redis.from_url(redis_url)
            redis_client.ping()  # 接続テスト
            logger.info("Successfully connected to Redis")
    except Exception as e:
        logger.warning(f"Redis接続エラー: {e}")
        logger.info("Redis無しモードで動作します")
        redis_client = None
    
    # アプリケーションにredisクライアント設定
    app.redis_client = redis_client
    app.notification_channel = notification_channel if redis_client else None
    
    # Redis無しモードの場合のフロントエンド通知用関数
    def send_notification_to_frontend(data):
        """Redis無しモードでフロントエンドにHTTP通知を送信"""
        try:
            frontend_url = os.environ.get("FRONTEND_URL", "http://notification:5002")
            if not frontend_url.endswith('/'):
                frontend_url += '/'
            
            notification_url = f"{frontend_url}api/notification"
            logger.info(f"Sending notification to frontend: {notification_url}")
            
            response = requests.post(
                notification_url,
                json=data,
                timeout=2  # 短いタイムアウト
            )
            
            if response.status_code == 200:
                logger.info("Notification sent to frontend successfully")
                return True
            else:
                logger.warning(f"Failed to send notification to frontend: {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"Error sending notification to frontend: {e}")
            return False
    
    # アプリケーションに関数を追加
    app.send_notification_to_frontend = send_notification_to_frontend
    
    # ルートの登録
    try:
        app.register_blueprint(slack_bp)
        logger.info("Registered slack blueprint")
    except Exception as e:
        logger.error(f"Failed to register slack blueprint: {e}")
    
    try:
        register_oauth_routes(app)
        logger.info("Registered OAuth routes")
    except Exception as e:
        logger.error(f"Failed to register OAuth routes: {e}")
        
    # 診断エンドポイント - アプリケーションの状態を確認
    @app.route('/debug/status')
    def debug_status():
        """アプリケーションの詳細なステータス情報"""
        status = {
            "app": "Smart Schedule Backend",
            "timestamp": datetime.now().isoformat(),
            "environment": os.environ.get("FLASK_ENV", "development"),
            "redis_connected": app.redis_client is not None,
            "redis_url": redis_url,
            "werkzeug_version": getattr(request, "werkzeug_version", "unknown"),
            "python_path": sys.path,
            "current_dir": os.getcwd(),
            "imported_modules": list(sys.modules.keys())
        }
        return jsonify(status)

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
            "timestamp": datetime.now().isoformat(),
            "redis_connected": app.redis_client is not None
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
        
        # Redis接続がある場合はRedisにイベントデータを保存
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
                
                # Redis通知に失敗した場合はHTTPでフロントエンドに直接通知
                notification_data = {
                    'type': 'event',
                    'event_id': event_id,
                    'summary': data.get('summary', '予定'),
                    'date': data.get('date', ''),
                    'time': data.get('time', ''),
                    'location': data.get('location', ''),
                    'description': data.get('description', '')
                }
                app.send_notification_to_frontend(notification_data)
        else:
            # Redis無しモードの場合はHTTPでフロントエンドに直接通知
            logger.info(f"Redis無しモード: HTTPでフロントエンドに通知: id={event_id}")
            notification_data = {
                'type': 'event',
                'event_id': event_id,
                'summary': data.get('summary', '予定'),
                'date': data.get('date', ''),
                'time': data.get('time', ''),
                'location': data.get('location', ''),
                'description': data.get('description', '')
            }
            app.send_notification_to_frontend(notification_data)
        
        return jsonify({"status": "ok", "event_id": event_id}), 201

    @app.route("/api/events/<event_id>", methods=["GET"])
    def get_event(event_id):
        """イベント情報取得エンドポイント"""
        if app.redis_client:
            event_data = app.redis_client.get(f"event:{event_id}")
            if not event_data:
                logger.warning(f"イベントが見つかりません: {event_id}")
                return jsonify({"error": "Event not found"}), 404
            
            return Response(event_data, mimetype='application/json')
        else:
            # Redis無しモードの場合（データはメモリに保持されないため、正確な対応は難しい）
            logger.warning(f"Redis無しモードでイベント取得: {event_id}")
            # この例ではダミーデータを返す
            dummy_data = {
                "event_id": event_id,
                "summary": "Redis無しモードのイベント",
                "date": datetime.now().strftime("%Y-%m-%d"),
                "time": datetime.now().strftime("%H:%M"),
                "description": "Redis無しモードではイベントデータを永続的に保存できません"
            }
            return jsonify(dummy_data)
    
    @app.route("/api/events/<event_id>/approve", methods=["POST"])
    def approve_event(event_id):
        """イベント承認処理エンドポイント"""
        try:
            # POSTデータの取得
            data = request.get_json()
            
            # Redis接続があればイベント情報を取得
            if app.redis_client:
                event_data_raw = app.redis_client.get(f"event:{event_id}")
                if event_data_raw:
                    data = json.loads(event_data_raw)
            
            # データのチェック
            if not data:
                logger.error("イベントデータが見つかりません")
                return jsonify({"error": "Event data not found"}), 400
                
            user_id = data.get("user_id")
            event_data = data.get("event_data")
            
            if not user_id or not event_data:
                logger.error(f"イベントデータ不足: user_id={user_id}, event_data={bool(event_data)}")
                return jsonify({"error": "Missing user_id or event_data"}), 400
            
            # Googleカレンダーにイベントを登録
            success, result = create_calendar_event(user_id, event_data)
            
            # 結果を通知
            notification = {
                'type': 'result',
                'success': success,
                'message': f"予定「{event_data.get('title', '予定')}」の登録が{'成功' if success else '失敗'}しました。"
            }
            
            # Redis経由で通知
            if app.redis_client:
                try:
                    app.redis_client.publish(app.notification_channel, json.dumps(notification))
                    # イベント情報をRedisから削除
                    app.redis_client.delete(f"event:{event_id}")
                except Exception as e:
                    logger.error(f"Redis通知送信エラー: {e}", exc_info=True)
                    # HTTP通知にフォールバック
                    app.send_notification_to_frontend(notification)
            else:
                # HTTP経由で直接フロントエンドに通知
                app.send_notification_to_frontend(notification)
            
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
        try:
            # POSTデータの取得
            data = request.get_json()
            
            # Redis接続があればイベント情報を取得
            if app.redis_client:
                event_data_raw = app.redis_client.get(f"event:{event_id}")
                if event_data_raw:
                    data = json.loads(event_data_raw)
            
            # データのチェック
            if not data:
                logger.error("イベントデータが見つかりません")
                return jsonify({"error": "Event data not found"}), 400
            
            # 結果を通知
            notification = {
                'type': 'result',
                'success': False,
                'message': f"予定「{data.get('summary', '予定')}」の登録がキャンセルされました。"
            }
            
            # Redis経由で通知
            if app.redis_client:
                try:
                    app.redis_client.publish(app.notification_channel, json.dumps(notification))
                    # イベント情報をRedisから削除
                    app.redis_client.delete(f"event:{event_id}")
                except Exception as e:
                    logger.error(f"Redis通知送信エラー: {e}", exc_info=True)
                    # HTTP通知にフォールバック
                    app.send_notification_to_frontend(notification)
            else:
                # HTTP経由で直接フロントエンドに通知
                app.send_notification_to_frontend(notification)
            
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
            
            logger.info(f"イベント拒否処理完了: id={event_id}")
            return jsonify({"status": "ok"})
        except Exception as e:
            logger.error(f"イベント拒否処理エラー: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500
    
    # フロントエンドからのHTTP通知エンドポイント（Redis代替用）
    @app.route('/api/notification', methods=['POST'])
    def receive_notification():
        """
        フロントエンド向けのHTTP通知エンドポイント
        Redis無しモードでの通信に使用
        """
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        logger.info(f"フロントエンドから通知を受信: {data}")
        
        # ここで通知を処理（実際には何もしない）
        return jsonify({"status": "ok"})
    
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
    logger.info(f"Starting Flask application on port {port}")
    app.run(host="0.0.0.0", port=port, debug=True)