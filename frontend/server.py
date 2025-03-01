#!/usr/bin/env python3
"""
スマート予定管理通知サーバー
Redisを使用してバックエンドからの通知を受け取り、WebSocketを通じてブラウザに転送します
"""
import os
import sys
import json
import time
import threading
import logging
import redis
import requests
from flask import Flask, render_template, request, jsonify, Response
from flask_socketio import SocketIO, emit

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('notification_server.log')
    ]
)
logger = logging.getLogger(__name__)

# 環境変数からRedis URLとバックエンドAPIのURLを取得
# Docker環境ではredisサービス名を使用、それ以外ではlocalhost
is_docker = os.getenv("DOCKER_ENV", "False").lower() in ("true", "1", "t", "yes")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0" if is_docker else "redis://localhost:6379/0")
BACKEND_URL = os.getenv("BACKEND_URL", "http://web:5001" if is_docker else "http://localhost:5001")
PORT = int(os.getenv("PORT", 5002))

# Redisの接続先をログに出力 (診断用)
logger.info(f"Redis接続URL: {REDIS_URL}")
logger.info(f"バックエンドURL: {BACKEND_URL}")
logger.info(f"Docker環境: {'はい' if is_docker else 'いいえ'}")

# Flaskアプリケーションの設定
app = Flask(__name__, 
            static_folder='static',
            template_folder='templates')
socketio = SocketIO(app, cors_allowed_origins="*")

# Redisへの接続を試みる関数
def connect_to_redis(max_retries=10, retry_interval=5):
    """
    Redisサーバーへの接続を試みる
    
    Args:
        max_retries (int): 最大再試行回数
        retry_interval (int): 再試行間隔（秒）
        
    Returns:
        redis.Redis or None: 接続成功時はRedisクライアント、失敗時はNone
    """
    for attempt in range(max_retries):
        try:
            logger.info(f"Redisへの接続を試行しています ({attempt+1}/{max_retries})...")
            # host, portを明示的に指定してみる
            if "redis://" in REDIS_URL:
                # redis://redis:6379/0 形式のURLからホスト名を抽出
                parts = REDIS_URL.replace("redis://", "").split(":")
                host = parts[0]
                port = int(parts[1].split("/")[0])
                logger.info(f"Redisに接続します: {host}:{port}")
                client = redis.Redis(host=host, port=port, socket_timeout=10)
            else:
                # URL形式で接続
                client = redis.from_url(REDIS_URL, socket_timeout=10)
                
            # 接続テスト
            client.ping()
            logger.info(f"Redisに接続しました: {REDIS_URL}")
            return client
        except redis.exceptions.ConnectionError as e:
            logger.warning(f"Redis接続エラー (試行 {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                logger.info(f"{retry_interval}秒後に再試行します...")
                time.sleep(retry_interval)
        except Exception as e:
            logger.error(f"Redisクライアント初期化エラー: {e}", exc_info=True)
            # IPアドレス名前解決を試す
            if "redis" in REDIS_URL:
                import socket
                try:
                    redis_ip = socket.gethostbyname("redis")
                    logger.info(f"Redis名前解決結果: redis -> {redis_ip}")
                except socket.gaierror:
                    logger.error("Redis名前解決に失敗しました")
            if attempt < max_retries - 1:
                time.sleep(retry_interval)
            else:
                break
    
    logger.error("Redisへの接続に失敗しました。フォールバックモードで実行します。")
    return None

# Redisクライアントの初期化（再試行あり）
redis_client = connect_to_redis()

# Redis利用ができるかどうかをチェック
redis_available = redis_client is not None

# 通知用のチャネル
NOTIFICATION_CHANNEL = "smart_scheduler_notifications"

# 接続されたクライアント管理用
connected_clients = {}

def redis_listener():
    """
    Redisパブサブからの通知を受け取ってSocketIOに転送
    このスレッドはバックグラウンドで実行され、Redisからの通知を監視します
    """
    if not redis_available:
        logger.warning("Redisに接続できないため、リスナーを開始できません")
        return
    
    # pubsubオブジェクトを作成し、チャンネルを購読
    pubsub = redis_client.pubsub()
    pubsub.subscribe(NOTIFICATION_CHANNEL)
    
    logger.info(f"Redis listener started on channel {NOTIFICATION_CHANNEL}")
    
    try:
        for message in pubsub.listen():
            if message['type'] == 'message':
                try:
                    data = json.loads(message['data'].decode('utf-8'))
                    logger.info(f"Received notification: {data}")
                    
                    # SocketIOを通じてブラウザクライアントに通知
                    socketio.emit('notification', data)
                    
                    # デスクトップクライアントが存在すれば、そちらにも通知
                    if data.get('type') == 'event':
                        forward_to_desktop_client(data)
                except Exception as e:
                    logger.error(f"Error processing message: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Redis listener error: {e}", exc_info=True)
        # 5秒後に再接続を試みる
        time.sleep(5)
        threading.Thread(target=redis_listener, daemon=True).start()

def forward_to_desktop_client(data):
    """
    デスクトップクライアントにイベントデータを転送（存在する場合）
    """
    try:
        # デスクトップクライアントのエンドポイントにPOST
        response = requests.post("http://localhost:5010/event", 
                                 json=data, 
                                 timeout=1)
        logger.info(f"Desktop client response: {response.status_code}")
    except requests.exceptions.ConnectTimeout:
        logger.debug("Desktop client not available (connection timeout)")
    except Exception as e:
        # デスクトップクライアントが存在しない場合は無視
        logger.debug(f"Desktop client not available: {e}")

@app.route('/')
def index():
    """メインページ - 通知を表示するページ"""
    return render_template('index.html')

@app.route('/event-popup/<event_id>')
def event_popup(event_id):
    """イベント通知用のポップアップページ"""
    return render_template('event_popup.html', event_id=event_id)

@app.route('/api/event/<event_id>', methods=['GET'])
def get_event(event_id):
    """
    イベント情報を取得
    
    Args:
        event_id: イベントの一意識別子
        
    Returns:
        イベント情報のJSON
    """
    # Redisが利用できない場合はバックエンドAPIを呼び出す
    if not redis_available:
        try:
            response = requests.get(f"{BACKEND_URL}/api/events/{event_id}")
            if response.status_code == 200:
                return Response(response.content, mimetype='application/json')
            else:
                return jsonify({"status": "error", "message": "Event not found"}), 404
        except Exception as e:
            logger.error(f"Backend API error: {e}", exc_info=True)
            return jsonify({"status": "error", "message": "Service unavailable"}), 503
    
    # Redisからイベント情報を取得
    event_data = redis_client.get(f"event:{event_id}")
    if not event_data:
        return jsonify({"status": "error", "message": "Event not found"}), 404
    
    return Response(event_data, mimetype='application/json')

@app.route('/api/event/<event_id>/approve', methods=['POST'])
def approve_event(event_id):
    """
    イベント承認処理
    
    Args:
        event_id: 承認するイベントのID
        
    Returns:
        処理結果のJSON
    """
    # イベント情報の取得
    if redis_available:
        event_data_raw = redis_client.get(f"event:{event_id}")
        if not event_data_raw:
            return jsonify({"status": "error", "message": "Event not found"}), 404
        
        event_data = json.loads(event_data_raw)
    else:
        # Redisが利用できない場合はリクエストボディを使用
        event_data = request.json
        if not event_data:
            return jsonify({"status": "error", "message": "No event data provided"}), 400
    
    # バックエンドサーバーに承認リクエストを転送
    try:
        response = requests.post(f"{BACKEND_URL}/api/events/{event_id}/approve", json=event_data)
        
        # Redisが利用可能ならイベント情報を削除
        if redis_available and response.status_code == 200:
            redis_client.delete(f"event:{event_id}")
            
        return Response(response.content, status=response.status_code, mimetype='application/json')
    except Exception as e:
        logger.error(f"Backend API error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Service unavailable: {str(e)}"}), 503

@app.route('/api/event/<event_id>/deny', methods=['POST'])
def deny_event(event_id):
    """
    イベント拒否処理
    
    Args:
        event_id: 拒否するイベントのID
        
    Returns:
        処理結果のJSON
    """
    # イベント情報の取得
    if redis_available:
        event_data_raw = redis_client.get(f"event:{event_id}")
        if not event_data_raw:
            return jsonify({"status": "error", "message": "Event not found"}), 404
        
        event_data = json.loads(event_data_raw)
    else:
        # Redisが利用できない場合はリクエストボディを使用
        event_data = request.json
        if not event_data:
            return jsonify({"status": "error", "message": "No event data provided"}), 400
    
    # バックエンドサーバーに拒否リクエストを転送
    try:
        response = requests.post(f"{BACKEND_URL}/api/events/{event_id}/deny", json=event_data)
        
        # Redisが利用可能ならイベント情報を削除
        if redis_available and response.status_code == 200:
            redis_client.delete(f"event:{event_id}")
            
        return Response(response.content, status=response.status_code, mimetype='application/json')
    except Exception as e:
        logger.error(f"Backend API error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Service unavailable: {str(e)}"}), 503

# サーバーステータス確認エンドポイント
@app.route('/health')
def health_check():
    """サーバーの健全性確認"""
    status = {
        "status": "healthy",
        "redis_connected": redis_available,
        "time": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    return jsonify(status)

@socketio.on('connect')
def handle_connect():
    """クライアント接続時の処理"""
    client_id = request.sid
    connected_clients[client_id] = {
        'connected_at': time.time(),
        'user_agent': request.headers.get('User-Agent', 'Unknown')
    }
    logger.info(f"Client connected: {client_id}")
    emit('welcome', {'message': 'Connected to notification server', 'redis_available': redis_available})

@socketio.on('disconnect')
def handle_disconnect():
    """クライアント切断時の処理"""
    client_id = request.sid
    if client_id in connected_clients:
        del connected_clients[client_id]
    logger.info(f"Client disconnected: {client_id}")

def main():
    """サーバーのメイン関数"""
    logger.info("スマート予定管理通知サーバーを起動しています...")
    
    # Redisリスナーを別スレッドで開始（Redisが利用可能な場合）
    if redis_available:
        redis_thread = threading.Thread(target=redis_listener, daemon=True)
        redis_thread.start()
        logger.info("Redisリスナーを開始しました")
    else:
        logger.warning("Redisに接続できないため、通知機能は制限されます")
    
    # サーバーのホストとポートを設定
    host = '0.0.0.0'
    logger.info(f"通知サーバーを {host}:{PORT} で起動します")
    
    # gunicornでの起動に適した形に変更
    return socketio

if __name__ == "__main__":
    # これが重要な変更点です
    socketio_app = main()
    
    # コマンドラインから直接起動する場合
    socketio_app.run(
        app, 
        host='0.0.0.0', 
        port=PORT, 
        debug=False, 
        use_reloader=False
    )