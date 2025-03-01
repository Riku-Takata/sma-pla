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
import socket
import subprocess
from datetime import datetime
import requests
from flask import Flask, render_template, request, jsonify, Response
from flask_socketio import SocketIO, emit

# Redis接続周りの強化
try:
    import redis
    REDIS_AVAILABLE_LIB = True
except ImportError:
    REDIS_AVAILABLE_LIB = False
    logging.error("Redisライブラリがインストールされていません。pip install redisを実行してください。")

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

# Redis接続の詳細なデバッグ情報を表示
logger.info(f"Redis接続URL: {REDIS_URL}")
logger.info(f"バックエンドURL: {BACKEND_URL}")
logger.info(f"Docker環境: {'はい' if is_docker else 'いいえ'}")
logger.info(f"現在の作業ディレクトリ: {os.getcwd()}")
logger.info(f"Pythonバージョン: {sys.version}")

# Flaskアプリケーションの設定
app = Flask(__name__, 
            static_folder='static',
            template_folder='templates')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# 通知用のチャネル
NOTIFICATION_CHANNEL = os.getenv("NOTIFICATION_CHANNEL", "smart_scheduler_notifications")

# イベントメッセージをメモリに一時保存（Redis代替機能）
memory_events = {}

# 接続されたクライアント管理用
connected_clients = {}

# サーバー起動時間の記録
server_start_time = time.time()

# Redis接続診断実行
def run_redis_diagnostics():
    """Redisへの接続に関する診断情報を収集"""
    diagnostics = {
        "hostname": socket.gethostname(),
        "ip_addresses": [],
        "docker_network": [],
        "dns_servers": [],
        "redis_url": REDIS_URL,
        "resolve_redis": None,
        "container_ips": [],
        "web_container_ip": None,
        "ping_redis": None,
        "backend_request_error": None
    }
    
    try:
        # ホストのIPアドレスを収集
        try:
            hostname = socket.gethostname()
            diagnostics["hostname"] = hostname
            ip_addr = socket.gethostbyname(hostname)
            diagnostics["ip_addresses"].append(ip_addr)
            
            # コンテナIPの収集
            try:
                container_ip = f"{ip_addr} {hostname}"
                diagnostics["container_ips"].append(container_ip)
            except:
                pass
        except:
            pass
        
        # バックエンドコンテナIPの解決を試みる
        try:
            backend_host = "web"
            try:
                backend_ip = socket.gethostbyname(backend_host)
                diagnostics["web_container_ip"] = f"{backend_host} -> {backend_ip}"
            except socket.gaierror:
                diagnostics["web_container_ip"] = f"解決失敗: {backend_host}"
        except:
            pass
            
        # バックエンドサーバーの状態を確認
        try:
            response = requests.get(f"{BACKEND_URL}/api/health?full_diagnostics=true", timeout=5)
            diagnostics["backend_health"] = response.json()
        except Exception as e:
            diagnostics["backend_request_error"] = str(e)
            
        # Dockerネットワーク情報
        try:
            if os.path.exists('/proc/net/route'):
                with open('/proc/net/route', 'r') as f:
                    diagnostics["docker_network"] = f.read().splitlines()
        except:
            pass
            
        # DNS設定を確認
        try:
            if os.path.exists('/etc/resolv.conf'):
                with open('/etc/resolv.conf', 'r') as f:
                    for line in f:
                        if line.startswith('nameserver'):
                            diagnostics["dns_servers"].append(line.strip())
        except:
            pass
        
        # Redisホスト名の解決を試みる
        try:
            if "redis://" in REDIS_URL:
                host = REDIS_URL.replace("redis://", "").split(":")[0]
                try:
                    redis_ip = socket.gethostbyname(host)
                    diagnostics["resolve_redis"] = f"{host} -> {redis_ip}"
                except socket.gaierror:
                    diagnostics["resolve_redis"] = f"解決失敗: {host}"
        except:
            pass
        
        # システムコマンドを使ってredisコンテナにpingを試みる
        try:
            if is_docker:
                ping_cmd = subprocess.run(
                    ["ping", "-c", "1", "redis"], 
                    capture_output=True, 
                    text=True, 
                    timeout=5
                )
                diagnostics["ping_redis"] = ping_cmd.stdout if ping_cmd.returncode == 0 else ping_cmd.stderr
        except:
            diagnostics["ping_redis"] = "実行失敗"
            
        logger.info(f"Redis診断情報: {json.dumps(diagnostics, indent=2)}")
        return diagnostics
    except Exception as e:
        logger.error(f"診断情報収集中にエラー: {e}")
        return diagnostics

# 改良版Redis接続関数 - 複数の方法を試みる
def connect_to_redis(max_retries=5, retry_interval=3):
    """
    複数の方法でRedisへの接続を試みる - DNS問題に対応
    
    Args:
        max_retries (int): 最大再試行回数
        retry_interval (int): 再試行間隔（秒）
        
    Returns:
        redis.Redis or None: 接続成功時はRedisクライアント、失敗時はNone
    """
    if not REDIS_AVAILABLE_LIB:
        logger.error("Redisライブラリがインストールされていません")
        return None
    
    # 診断情報を収集
    run_redis_diagnostics()
    
    # 方法1: 通常のURL接続（最もシンプル）
    logger.info("Redis接続方法1を試行中...")
    for attempt in range(2):  # 2回試行
        try:
            logger.info(f"Redis URLでの接続を試行: {REDIS_URL}")
            client = redis.from_url(
                REDIS_URL, 
                socket_timeout=20,
                socket_connect_timeout=20,
                health_check_interval=15,
                retry_on_timeout=True
            )
            
            # 接続テスト
            if client.ping():
                logger.info(f"✅ Redisに接続しました: {REDIS_URL}")
                return client
                
        except redis.exceptions.ConnectionError as e:
            logger.warning(f"Redis URL接続エラー: {e}")
            time.sleep(3)  # 少し待機
        except Exception as e:
            logger.error(f"Redis接続エラー: {e}", exc_info=True)
            time.sleep(3)  # 少し待機
    
    # 方法2: バックエンドサーバーからRedisの情報を取得
    logger.info("Redis接続方法2を試行中...")
    try:
        # バックエンドサーバーからRedis情報を取得
        response = requests.get(f"{BACKEND_URL}/api/health", timeout=20)
        data = response.json()
        if data.get("redis_ip"):
            redis_ip = data["redis_ip"]
            redis_port = data.get("redis_port", 6379)
            logger.info(f"バックエンドからRedis IPを取得: {redis_ip}:{redis_port}")
            
            # IPアドレスで直接接続
            client = redis.Redis(
                host=redis_ip,
                port=redis_port,
                socket_timeout=20,
                socket_connect_timeout=20,
                retry_on_timeout=True
            )
            
            # 接続テスト
            if client.ping():
                logger.info(f"✅ Redisに接続しました（接続方法2）")
                return client
    except Exception as e:
        logger.error(f"バックエンドサーバーからの情報取得エラー: {e}")
    
    # 方法3: IPスキャンによるRedisサービスの自動発見
    logger.info("Redis接続方法3を試行中...")
    try:
        # 現在のコンテナIPを取得
        current_ip = None
        try:
            hostname = socket.gethostname()
            current_ip = socket.gethostbyname(hostname)
            logger.info(f"現在のコンテナIP: {current_ip}")
        except:
            pass
        
        # 同じサブネット上でRedisを探索
        if current_ip and current_ip.startswith("172."):
            subnet_prefix = ".".join(current_ip.split(".")[:3])
            logger.info(f"サブネット {subnet_prefix}.0/24 をスキャン中...")
            
            # 一般的なコンテナIP範囲をスキャン
            for i in range(2, 10):  # 通常2-10がサービスコンテナに割り当てられることが多い
                ip = f"{subnet_prefix}.{i}"
                logger.info(f"IPをテスト中: {ip}")
                
                try:
                    # IPアドレスに直接接続を試みる
                    client = redis.Redis(
                        host=ip,
                        port=6379,
                        socket_timeout=5,
                        socket_connect_timeout=3
                    )
                    
                    # 接続テスト
                    if client.ping():
                        # より長いタイムアウトで本番用クライアントを作成
                        prod_client = redis.Redis(
                            host=ip,
                            port=6379,
                            socket_timeout=20,
                            socket_connect_timeout=20,
                            retry_on_timeout=True
                        )
                        logger.info(f"✅ Redis接続に成功: {ip}:6379")
                        logger.info(f"✅ Redisに接続しました（接続方法3）")
                        return prod_client
                except:
                    # このIPでは接続できなかった - 次へ
                    continue
    except Exception as e:
        logger.error(f"RedisサービスIPスキャン中のエラー: {e}")
    
    # すべての方法が失敗
    logger.error("すべてのRedis接続方法が失敗しました")
    return None

# Redisクライアントの初期化（再試行あり）
redis_client = None
try:
    redis_client = connect_to_redis()
except Exception as e:
    logger.error(f"Redis接続の初期化中にエラーが発生しました: {e}", exc_info=True)
    logger.warning("Redis無しモードで実行します")

# Redis利用ができるかどうかをチェック
redis_available = redis_client is not None

# Redis操作を安全に実行するユーティリティ関数
def safe_redis_operation(operation_func, fallback_value=None, max_retries=3):
    """
    Redisの操作を安全に実行するユーティリティ関数
    
    Args:
        operation_func: 実行するRedis操作の関数（引数はredis_client）
        fallback_value: 失敗時に返す値
        max_retries: 最大再試行回数
        
    Returns:
        操作の結果、または失敗時はfallback_value
    """
    global redis_client, redis_available
    
    # Redisが利用できない場合は即座にフォールバック
    if not redis_available or not redis_client:
        return fallback_value
    
    for attempt in range(max_retries):
        try:
            return operation_func(redis_client)
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as e:
            logger.warning(f"Redis操作エラー（試行 {attempt+1}/{max_retries}）: {e}")
            
            # 最終試行以外は再試行
            if attempt < max_retries - 1:
                time.sleep(1)  # 少し待機
                
                # クライアント状態を確認し、必要なら再接続
                try:
                    # reconnect_attempts属性がない場合は初期化
                    if not hasattr(safe_redis_operation, 'reconnect_attempts'):
                        safe_redis_operation.reconnect_attempts = 0
                    
                    # 再接続試行回数が多すぎる場合はスキップ
                    if safe_redis_operation.reconnect_attempts >= 5:
                        logger.warning("再接続試行回数が上限に達しました")
                        continue
                        
                    # 接続が生きているか確認
                    try:
                        redis_client.ping()
                    except:
                        raise redis.exceptions.ConnectionError("Connection check failed")
                except:
                    logger.warning("Redis接続が切断されています。再接続を試みます...")
                    safe_redis_operation.reconnect_attempts += 1
                    
                    try:
                        new_client = connect_to_redis(max_retries=1)
                        if new_client:
                            redis_client = new_client
                            redis_available = True
                            logger.info("Redis再接続に成功しました")
                        else:
                            redis_available = False
                    except:
                        logger.error("Redis再接続に失敗しました")
                        redis_available = False
            else:
                # 最大試行回数に達した場合
                logger.error("Redis操作の最大再試行回数に達しました")
        except Exception as e:
            logger.error(f"予期しないRedisエラー: {e}", exc_info=True)
            break
    
    return fallback_value

# 改良版Redisリスナー
def redis_listener(reconnect_attempts=0):
    """
    改良版Redisパブサブリスナー
    再接続と耐障害性を強化
    
    Args:
        reconnect_attempts (int): 再接続試行回数
    """
    global redis_client, redis_available
    
    # Redisクライアントのチェック
    if not redis_client or not redis_available:
        logger.warning("Redisクライアントがないため、リスナーを起動できません")
        return
    
    # 最大再接続回数の制限（無限ループを防ぐ）
    if reconnect_attempts > 50:
        logger.error("Redis接続の最大再試行回数に達しました。リスナーを一時停止します。")
        
        # 一定時間後に再試行
        time.sleep(300)  # 5分待機
        
        # 新しいRedisクライアントで再接続
        try:
            new_client = connect_to_redis()
            if new_client:
                redis_client = new_client
                redis_available = True
                logger.info("Redis接続を回復しました。リスナーを再起動します。")
                # リスナーを0回の再接続試行からやり直す
                return redis_listener(0)
            else:
                # 再接続に失敗したが、まだ試行を続ける
                return redis_listener(reconnect_attempts + 1)
        except Exception as e:
            logger.error(f"Redis回復エラー: {e}")
            return redis_listener(reconnect_attempts + 1)
    
    try:
        # 新しいpubsubオブジェクトを作成
        pubsub = redis_client.pubsub()
        pubsub.subscribe(NOTIFICATION_CHANNEL)
        
        # 明示的に接続とsubscribeを確認
        first_message = pubsub.get_message(timeout=5)
        if not first_message or first_message.get('type') != 'subscribe':
            logger.warning(f"Subscribe確認メッセージが受信できませんでした: {first_message}")
            raise redis.exceptions.ConnectionError("Subscribe confirmation failed")
        
        logger.info(f"Redisリスナーを開始しました (チャンネル: {NOTIFICATION_CHANNEL}, 試行: {reconnect_attempts})")
        
        # 最後のヘルスチェック時刻を初期化
        last_health_check = time.time()
        last_message_time = time.time()
        
        # メッセージ処理ループ
        while True:
            try:
                # タイムアウト付きでメッセージを取得（ブロックを防ぐ）
                message = pubsub.get_message(timeout=1.0)
                
                if message:
                    last_message_time = time.time()
                    
                    if message['type'] == 'message':
                        try:
                            data = json.loads(message['data'].decode('utf-8'))
                            logger.info(f"通知を受信: {data}")
                            
                            # SocketIOでクライアントに通知
                            socketio.emit('notification', data)
                            
                            # デスクトップクライアントへの転送
                            if data.get('type') == 'event':
                                forward_to_desktop_client(data)
                                
                                # メモリキャッシュ
                                if 'event_id' in data:
                                    memory_events[data['event_id']] = data
                        except Exception as msg_err:
                            logger.error(f"メッセージ処理エラー: {msg_err}", exc_info=True)
                
                # ヘルスチェック
                current_time = time.time()
                
                # 30秒ごとのヘルスチェック
                if current_time - last_health_check > 30:
                    try:
                        redis_client.ping()
                        last_health_check = current_time
                        logger.debug("Redisヘルスチェック: OK")
                    except Exception as health_err:
                        logger.warning(f"Redisヘルスチェックに失敗: {health_err}")
                        raise redis.exceptions.ConnectionError("Health check failed")
                
                # 長時間メッセージがない場合はチャネル再購読を検討
                if current_time - last_message_time > 600:  # 10分間メッセージがない
                    logger.info("長時間メッセージがないためチャンネルを再購読します")
                    try:
                        # 古い接続を閉じる
                        pubsub.unsubscribe()
                        pubsub.close()
                        
                        # 新しい接続を作成
                        pubsub = redis_client.pubsub()
                        pubsub.subscribe(NOTIFICATION_CHANNEL)
                        pubsub.get_message()  # subscribeメッセージを受け取る
                        
                        last_message_time = current_time
                        logger.info("チャンネルの再購読に成功しました")
                    except Exception as resub_err:
                        logger.error(f"チャンネル再購読エラー: {resub_err}")
                        raise redis.exceptions.ConnectionError("Channel resubscription failed")
                        
            except redis.exceptions.TimeoutError:
                # タイムアウトは正常なので無視（次のループへ）
                continue
                
            except redis.exceptions.ConnectionError as conn_err:
                logger.error(f"Redis接続エラー: {conn_err}")
                # 接続を閉じて、再接続試行
                try:
                    pubsub.close()
                except:
                    pass
                
                # 再接続前に少し待つ
                time.sleep(5)
                
                # 再帰的に新しいリスナーを開始（試行回数を増加）
                return redis_listener(reconnect_attempts + 1)
                
            except Exception as loop_err:
                logger.error(f"Redisリスナーループエラー: {loop_err}", exc_info=True)
                # 一般的なエラーの場合も再接続を試みる
                try:
                    pubsub.close()
                except:
                    pass
                time.sleep(5)
                return redis_listener(reconnect_attempts + 1)
    
    except Exception as e:
        logger.error(f"Redisリスナー初期化エラー: {e}", exc_info=True)
        time.sleep(5)
        return redis_listener(reconnect_attempts + 1)

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
    イベント情報を取得（改良版）
    
    Args:
        event_id: イベントの一意識別子
        
    Returns:
        イベント情報のJSON
    """
    # メモリキャッシュをまず確認
    if event_id in memory_events:
        logger.info(f"メモリからイベント情報を取得: {event_id}")
        return jsonify(memory_events[event_id])
    
    # Redisから取得を試みる
    def get_from_redis(r):
        data = r.get(f"event:{event_id}")
        if data:
            return Response(data, mimetype='application/json')
        return None
    
    redis_result = safe_redis_operation(get_from_redis)
    if redis_result:
        return redis_result
    
    # Redisからの取得に失敗した場合はバックエンドAPIにフォールバック
    try:
        response = requests.get(f"{BACKEND_URL}/api/events/{event_id}")
        if response.status_code == 200:
            # 成功したら結果をメモリにキャッシュ
            try:
                data = response.json()
                if data and 'event_id' in data:
                    memory_events[event_id] = data
            except:
                pass
            return Response(response.content, mimetype='application/json')
        else:
            return jsonify({"status": "error", "message": "Event not found"}), 404
    except Exception as e:
        logger.error(f"Backend API error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Service unavailable"}), 503

@app.route('/api/event/<event_id>/approve', methods=['POST'])
def approve_event(event_id):
    """
    イベント承認処理（改良版）
    
    Args:
        event_id: 承認するイベントのID
        
    Returns:
        処理結果のJSON
    """
    # イベントデータを収集
    event_data = None
    
    # メモリキャッシュから取得
    if event_id in memory_events:
        event_data = memory_events[event_id]
        logger.info(f"メモリからイベントデータを取得: {event_id}")
    
    # Redisから取得
    if not event_data and redis_available:
        def get_event_from_redis(r):
            data = r.get(f"event:{event_id}")
            if data:
                try:
                    return json.loads(data)
                except:
                    return None
            return None
        
        event_data = safe_redis_operation(get_event_from_redis)
        if event_data:
            logger.info(f"Redisからイベントデータを取得: {event_id}")
    
    # リクエストボディから取得
    if not event_data:
        event_data = request.json
        logger.info(f"リクエストボディからイベントデータを取得: {event_id}")
    
    # データ検証
    if not event_data:
        logger.error(f"イベントデータが見つかりません: {event_id}")
        return jsonify({"status": "error", "message": "イベントデータが見つかりません"}), 400
    
    # バックエンドAPIに承認リクエストを転送
    try:
        response = requests.post(
            f"{BACKEND_URL}/api/events/{event_id}/approve", 
            json=event_data,
            timeout=10  # 10秒のタイムアウト
        )
        
        # エラーチェック
        if response.status_code >= 400:
            logger.error(f"バックエンドAPI承認エラー: {response.status_code} {response.text}")
            return jsonify({"status": "error", "message": f"API error: {response.status_code}"}), response.status_code
            
        # 成功したらイベントデータをクリーンアップ
        if event_id in memory_events:
            del memory_events[event_id]
            logger.info(f"メモリキャッシュからイベントを削除: {event_id}")
            
        # Redisからのクリーンアップ
        if redis_available:
            def delete_from_redis(r):
                r.delete(f"event:{event_id}")
                logger.info(f"Redisからイベントを削除: {event_id}")
                return True
            
            safe_redis_operation(delete_from_redis, fallback_value=False)
            
        # 通知をクライアントに送信
        try:
            # 結果通知
            socketio.emit('notification', {
                'type': 'result',
                'success': True,
                'message': f"予定「{event_data.get('summary', '予定')}」が承認されました"
            })
        except Exception as notify_err:
            logger.error(f"結果通知エラー: {notify_err}")
            
        return Response(response.content, status=response.status_code, mimetype='application/json')
    except requests.exceptions.RequestException as e:
        logger.error(f"バックエンドAPI通信エラー: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"通信エラー: {str(e)}"}), 503
    except Exception as e:
        logger.error(f"予期しないエラー: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"予期しないエラー: {str(e)}"}), 500

@app.route('/api/event/<event_id>/deny', methods=['POST'])
def deny_event(event_id):
    """
    イベント拒否処理（改良版）
    
    Args:
        event_id: 拒否するイベントのID
        
    Returns:
        処理結果のJSON
    """
    # イベントデータを収集
    event_data = None
    
    # メモリキャッシュから取得
    if event_id in memory_events:
        event_data = memory_events[event_id]
        logger.info(f"メモリからイベントデータを取得: {event_id}")
    
    # Redisから取得
    if not event_data and redis_available:
        def get_event_from_redis(r):
            data = r.get(f"event:{event_id}")
            if data:
                try:
                    return json.loads(data)
                except:
                    return None
            return None
        
        event_data = safe_redis_operation(get_event_from_redis)
        if event_data:
            logger.info(f"Redisからイベントデータを取得: {event_id}")
    
    # リクエストボディから取得
    if not event_data:
        event_data = request.json
        logger.info(f"リクエストボディからイベントデータを取得: {event_id}")
    
    # データ検証
    if not event_data:
        logger.error(f"イベントデータが見つかりません: {event_id}")
        return jsonify({"status": "error", "message": "イベントデータが見つかりません"}), 400
    
    # バックエンドAPIに拒否リクエストを転送
    try:
        response = requests.post(
            f"{BACKEND_URL}/api/events/{event_id}/deny", 
            json=event_data,
            timeout=10  # 10秒のタイムアウト
        )
        
        # エラーチェック
        if response.status_code >= 400:
            logger.error(f"バックエンドAPI拒否エラー: {response.status_code} {response.text}")
            return jsonify({"status": "error", "message": f"API error: {response.status_code}"}), response.status_code
            
        # 成功したらイベントデータをクリーンアップ
        if event_id in memory_events:
            del memory_events[event_id]
            logger.info(f"メモリキャッシュからイベントを削除: {event_id}")
            
        # Redisからのクリーンアップ
        if redis_available:
            def delete_from_redis(r):
                r.delete(f"event:{event_id}")
                logger.info(f"Redisからイベントを削除: {event_id}")
                return True
            
            safe_redis_operation(delete_from_redis, fallback_value=False)
            
        # 通知をクライアントに送信
        try:
            # 結果通知
            socketio.emit('notification', {
                'type': 'result',
                'success': False,
                'message': f"予定「{event_data.get('summary', '予定')}」は拒否されました"
            })
        except Exception as notify_err:
            logger.error(f"結果通知エラー: {notify_err}")
            
        return Response(response.content, status=response.status_code, mimetype='application/json')
    except requests.exceptions.RequestException as e:
        logger.error(f"バックエンドAPI通信エラー: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"通信エラー: {str(e)}"}), 503
    except Exception as e:
        logger.error(f"予期しないエラー: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"予期しないエラー: {str(e)}"}), 500

# Redis無しモード用のHTTP通知エンドポイント
@app.route('/api/notification', methods=['POST'])
def receive_notification():
    """
    バックエンドからHTTP通知を受け取るエンドポイント（改良版）
    Redis無しモードで使用
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        logger.info(f"HTTP通知を受信: {data}")
        
        # イベントデータをメモリにキャッシュ（必要に応じて）
        if data.get('type') == 'event' and 'event_id' in data:
            memory_events[data['event_id']] = data
        
        # SocketIOを通じてブラウザクライアントに通知
        try:
            socketio.emit('notification', data)
            logger.info("通知をクライアントに転送しました")
        except Exception as e:
            logger.error(f"SocketIO通知エラー: {e}", exc_info=True)
        
        # デスクトップクライアントにも通知（必要に応じて）
        if data.get('type') == 'event':
            try:
                forward_to_desktop_client(data)
            except Exception as e:
                logger.error(f"デスクトップクライアント通知エラー: {e}", exc_info=True)
        
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.error(f"HTTP通知処理エラー: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

# サーバーステータス確認エンドポイント
@app.route('/health')
def health_check():
    """サーバーの健全性確認（改良版）"""
    global redis_client, redis_available
    
    # Redis接続状態を確認
    redis_status = "disconnected"
    redis_ping = None
    
    if redis_client and redis_available:
        try:
            redis_ping = redis_client.ping()
            redis_status = "connected" if redis_ping else "error"
        except Exception as e:
            redis_status = f"error: {str(e)}"
            
            # 接続に問題がある場合は再接続を試みる
            try:
                new_client = connect_to_redis(max_retries=1)
                if new_client:
                    redis_client = new_client
                    redis_available = True
                    redis_status = "reconnected"
            except:
                redis_available = False
    
    # 診断情報を収集
    diagnostics = run_redis_diagnostics() if request.args.get('full_diagnostics') else None
    
    status = {
        "status": "healthy",
        "redis_connected": redis_available,
        "redis_ping": redis_ping,
        "redis_status": redis_status,
        "time": datetime.now().isoformat(),
        "memory_events_count": len(memory_events),
        "connected_clients": len(connected_clients),
        "server_uptime": int(time.time() - server_start_time),
        "redis_url": REDIS_URL,
        "is_docker": is_docker,
        "backend_url": BACKEND_URL,
        "diagnostics": diagnostics
    }
    
    return jsonify(status)

@socketio.on('connect')
def handle_connect():
    """クライアント接続時の処理"""
    client_id = request.sid
    connected_clients[client_id] = {
        'connected_at': time.time(),
        'user_agent': request.headers.get('User-Agent', 'Unknown'),
        'ip': request.remote_addr
    }
    logger.info(f"クライアント接続: {client_id}")
    emit('welcome', {
        'message': 'Connected to notification server', 
        'redis_available': redis_available,
        'server_time': datetime.now().isoformat()
    })

@socketio.on('disconnect')
def handle_disconnect():
    """クライアント切断時の処理"""
    client_id = request.sid
    if client_id in connected_clients:
        del connected_clients[client_id]
    logger.info(f"クライアント切断: {client_id}")

def main():
    """サーバーのメイン関数（改良版）"""
    logger.info("スマート予定管理通知サーバーを起動しています...")
    
    global redis_client, redis_available, BACKEND_URL
    
    # Redis接続状態の診断
    if redis_available and redis_client:
        try:
            # 起動時の接続チェック
            redis_client.ping()
            logger.info("Redisサーバーが応答しています")
            
            # サーバー情報の取得
            try:
                info = redis_client.info()
                logger.info(f"Redis情報: バージョン={info.get('redis_version', '不明')}, "
                            f"メモリ使用量={info.get('used_memory_human', '不明')}, "
                            f"接続クライアント数={info.get('connected_clients', '不明')}")
                
                # 接続に成功したRedisのIPアドレスを環境変数に保存（再接続のため）
                if hasattr(redis_client, 'connection_pool') and hasattr(redis_client.connection_pool, 'connection_kwargs'):
                    redis_host = redis_client.connection_pool.connection_kwargs.get('host')
                    if redis_host:
                        logger.info(f"Redis IPアドレスを環境変数に保存: {redis_host}")
                        os.environ['REDIS_HOST'] = redis_host
            except Exception as info_err:
                logger.warning(f"Redisサーバー情報の取得に失敗: {info_err}")
            
            # Redisリスナーを別スレッドで開始
            redis_thread = threading.Thread(target=redis_listener, daemon=True)
            redis_thread.start()
            logger.info("Redisリスナーを開始しました")
            
        except Exception as e:
            logger.error(f"Redis診断中にエラーが発生しました: {e}", exc_info=True)
            logger.warning("Redis無しモードに切り替えます")
            redis_available = False
            redis_client = None
    
    # バックエンドの名前解決問題を解決
    if 'web' in BACKEND_URL:
        try:
            # バックエンドホスト名を解決
            backend_host = 'web'
            try:
                backend_ip = socket.gethostbyname(backend_host)
                logger.info(f"バックエンドIP解決成功: {backend_host} -> {backend_ip}")
            except socket.gaierror:
                logger.warning(f"バックエンドの名前解決に失敗: {backend_host}")
                
                # IPスキャンでバックエンドを見つける
                logger.info(f"バックエンド検索: サブネット 172.28.0.0/24 をスキャン中...")
                for i in range(2, 10):  # 一般的なコンテナIP範囲
                    test_ip = f"172.28.0.{i}"
                    try:
                        if test_ip != os.environ.get('REDIS_HOST'):  # すでに見つかったRedisと異なるIPをテスト
                            logger.info(f"バックエンドIPテスト: {test_ip}")
                            response = requests.get(f"http://{test_ip}:5001/api/health", timeout=3)
                            if response.status_code == 200:
                                logger.info(f"✅ バックエンドサーバー発見: {test_ip}")
                                backend_ip = test_ip
                                # URLを更新
                                BACKEND_URL = BACKEND_URL.replace("web", backend_ip)
                                logger.info(f"バックエンドURLをIPアドレスに更新: {BACKEND_URL}")
                                break
                    except:
                        continue
        except Exception as e:
            logger.error(f"バックエンド検索エラー: {e}")
    
    if not redis_available:
        logger.warning("Redisに接続できないため、Redis無しモードで動作します")
        logger.info("HTTP通知エンドポイントを使用してバックエンドと通信します")
        
        # 定期的なRedis再接続チェックタイマー起動
        def scheduled_reconnect():
            global redis_client, redis_available
            
            reconnect_interval = 60  # 最初は1分ごとに再試行
            max_reconnect_interval = 300  # 最大5分まで間隔を延長
            
            while True:
                time.sleep(reconnect_interval)
                
                try:
                    logger.info(f"Redis再接続を試みています... (間隔: {reconnect_interval}秒)")
                    new_client = connect_to_redis(max_retries=1)
                    
                    if new_client:
                        redis_client = new_client
                        redis_available = True
                        logger.info("Redis再接続に成功しました！リスナーを起動します")
                        
                        # リスナー開始
                        redis_thread = threading.Thread(target=redis_listener, daemon=True)
                        redis_thread.start()
                        break  # 接続成功したらループを抜ける
                    else:
                        # 次回の再試行間隔を延長（最大5分まで）
                        reconnect_interval = min(reconnect_interval * 1.5, max_reconnect_interval)
                        logger.warning(f"Redis再接続に失敗しました。次回は{reconnect_interval}秒後に再試行します。")
                except Exception as e:
                    logger.warning(f"Redis再接続試行中にエラー: {e}")
                    # 次回の再試行間隔を延長（最大5分まで）
                    reconnect_interval = min(reconnect_interval * 1.5, max_reconnect_interval)
        
        # 再接続スレッドを開始
        reconnect_thread = threading.Thread(target=scheduled_reconnect, daemon=True)
        reconnect_thread.start()
    
    # サーバー起動情報
    host = '0.0.0.0'
    logger.info(f"通知サーバーを {host}:{PORT} で起動します")
    logger.info(f"Redis状態: {'接続済み' if redis_available else '未接続'}")
    
    # Gunicornとの統合のため、socketioオブジェクト自体を返す
    return socketio

# SocketIOアプリケーション
application = socketio

if __name__ == "__main__":
    # Flaskアプリケーションを起動
    socketio_app = main()
    socketio_app.run(
        app, 
        host='0.0.0.0', 
        port=PORT, 
        debug=False, 
        use_reloader=False
    )