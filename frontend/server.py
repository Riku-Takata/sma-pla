#!/usr/bin/env python3
"""
スマート予定管理通知サーバー
Redisを使用してバックエンドからの通知を受け取り、WebSocketを通じてブラウザに転送します
"""
import os
import json
import time
import threading
import redis
import requests
from flask import Flask, render_template, request, jsonify, Response
from flask_socketio import SocketIO, emit

# 環境変数からRedis URLとバックエンドAPIのURLを取得
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:5001")

# Flaskアプリケーションの設定
app = Flask(__name__, 
            static_folder='static',
            template_folder='templates')
socketio = SocketIO(app, cors_allowed_origins="*")

# Redisクライアントの初期化
redis_client = redis.from_url(REDIS_URL)

# 通知用のチャネル
NOTIFICATION_CHANNEL = "smart_scheduler_notifications"

# 接続されたクライアント管理用
connected_clients = {}

def redis_listener():
    """
    Redisパブサブからの通知を受け取ってSocketIOに転送
    このスレッドはバックグラウンドで実行され、Redisからの通知を監視します
    """
    pubsub = redis_client.pubsub()
    pubsub.subscribe(NOTIFICATION_CHANNEL)
    
    print(f"Starting Redis listener on channel {NOTIFICATION_CHANNEL}")
    
    for message in pubsub.listen():
        if message['type'] == 'message':
            try:
                data = json.loads(message['data'].decode('utf-8'))
                print(f"Received notification: {data}")
                
                # SocketIOを通じてブラウザクライアントに通知
                socketio.emit('notification', data)
                
                # デスクトップクライアントが存在すれば、そちらにも通知
                if data.get('type') == 'event':
                    forward_to_desktop_client(data)
            except Exception as e:
                print(f"Error processing message: {e}")

def forward_to_desktop_client(data):
    """
    デスクトップクライアントにイベントデータを転送（存在する場合）
    """
    try:
        # デスクトップクライアントのエンドポイントにPOST
        response = requests.post("http://localhost:5010/new_event", 
                                 json=data, 
                                 timeout=1)
        print(f"Desktop client response: {response.status_code}")
    except Exception as e:
        # デスクトップクライアントが存在しない場合は無視
        print(f"Desktop client not available: {e}")

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
    # Redisからイベント情報を取得
    event_data_raw = redis_client.get(f"event:{event_id}")
    if not event_data_raw:
        return jsonify({"status": "error", "message": "Event not found"}), 404
    
    event_data = json.loads(event_data_raw)
    
    # バックエンドサーバーに承認リクエストを転送
    try:
        response = requests.post(f"{BACKEND_URL}/approve_event", json=event_data)
        if response.status_code == 200:
            # イベント情報をRedisから削除
            redis_client.delete(f"event:{event_id}")
            return jsonify(response.json())
        else:
            return jsonify({
                "status": "error", 
                "message": f"Backend error: {response.status_code}", 
                "details": response.text
            }), response.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/event/<event_id>/deny', methods=['POST'])
def deny_event(event_id):
    """
    イベント拒否処理
    
    Args:
        event_id: 拒否するイベントのID
        
    Returns:
        処理結果のJSON
    """
    # Redisからイベント情報を取得
    event_data_raw = redis_client.get(f"event:{event_id}")
    if not event_data_raw:
        return jsonify({"status": "error", "message": "Event not found"}), 404
    
    event_data = json.loads(event_data_raw)
    
    # バックエンドサーバーに拒否リクエストを転送
    try:
        response = requests.post(f"{BACKEND_URL}/deny_event", json=event_data)
        if response.status_code == 200:
            # イベント情報をRedisから削除
            redis_client.delete(f"event:{event_id}")
            return jsonify(response.json())
        else:
            return jsonify({
                "status": "error", 
                "message": f"Backend error: {response.status_code}", 
                "details": response.text
            }), response.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@socketio.on('connect')
def handle_connect():
    """クライアント接続時の処理"""
    client_id = request.sid
    connected_clients[client_id] = {
        'connected_at': time.time(),
        'user_agent': request.headers.get('User-Agent', 'Unknown')
    }
    print(f"Client connected: {client_id}")
    emit('welcome', {'message': 'Connected to notification server'})

@socketio.on('disconnect')
def handle_disconnect():
    """クライアント切断時の処理"""
    client_id = request.sid
    if client_id in connected_clients:
        del connected_clients[client_id]
    print(f"Client disconnected: {client_id}")

if __name__ == '__main__':
    # Redisリスナーを別スレッドで開始
    redis_thread = threading.Thread(target=redis_listener, daemon=True)
    redis_thread.start()
    
    # サーバーのホストとポートを設定
    host = '0.0.0.0'
    port = int(os.getenv('PORT', 5002))
    print(f"Starting notification server on {host}:{port}")
    
    # SocketIOサーバーを起動
    socketio.run(app, host=host, port=port, debug=False, allow_unsafe_werkzeug=True)