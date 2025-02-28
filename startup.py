#!/usr/bin/env python3
"""
スマート予定管理システム 統合起動スクリプト
Docker, ngrok, バックエンド、フロントエンド、デスクトップクライアントを管理
"""

import os
import sys
import subprocess
import platform
import json
import re
import time
import webbrowser

def run_command(command, capture_output=True):
    """コマンドを実行して結果を返す"""
    try:
        if capture_output:
            result = subprocess.run(
                command, 
                shell=True, 
                check=True, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE,
                text=True
            )
            return result.stdout.strip()
        else:
            subprocess.run(command, shell=True, check=True)
            return None
    except subprocess.CalledProcessError as e:
        print(f"コマンド実行エラー: {command}")
        print(f"エラー出力: {e.stderr if hasattr(e, 'stderr') else str(e)}")
        return None

def check_dependencies():
    """必要な依存関係をチェック"""
    dependencies = {
        "docker": "docker --version",
        "docker-compose": "docker-compose --version",
        "ngrok": "ngrok version"
    }
    
    missing_deps = []
    for dep, cmd in dependencies.items():
        result = run_command(cmd)
        if result is None:
            missing_deps.append(dep)
            print(f"❌ {dep.capitalize()}が見つかりません。インストールしてください。")
        else:
            print(f"✅ {dep.capitalize()}: {result}")
    
    if missing_deps:
        return False
    return True

def check_and_create_env_file():
    """環境設定ファイルの確認と作成"""
    if not os.path.exists(".env"):
        print("⚠️ .envファイルが見つかりません。.env.exampleからコピーします。")
        
        try:
            with open(".env.example", "r") as src, open(".env", "w") as dst:
                dst.write(src.read())
            print("✅ .env.exampleを.envにコピーしました。")
        except FileNotFoundError:
            print("❌ .env.exampleが見つかりません。手動で設定ファイルを作成してください。")
            return False
    
    return True

def create_bind_mount_compose_file():
    """
    バインドマウント用のdocker-compose-bind.ymlを生成
    """
    try:
        with open("docker-compose.yml", "r") as f:
            compose_data = f.read()
        
        # volumes定義をバインドマウントに置き換え
        compose_data = compose_data.replace(
            "volumes:\n      - redis-data:/data", 
            "volumes:\n      - ./redis-data:/data"
        )
        
        # volumes:セクションを除去
        import re
        compose_data = re.sub(
            r"volumes:\n  redis-data:\n    driver: local", 
            "", 
            compose_data
        )
        
        # ファイルに書き出し
        with open("docker-compose-bind.yml", "w") as f:
            f.write(compose_data)
        
        # redis-dataディレクトリを作成
        os.makedirs("redis-data", exist_ok=True)
        
        return True
    except Exception as e:
        print(f"⚠️ バインドマウント設定ファイルの生成に失敗: {e}")
        return False

def cleanup_redis_volume():
    """
    Redisボリュームをクリーンアップするための関数
    既存のRedisデータボリュームを削除して問題を解決
    Windows/Linux/Macに対応
    
    Returns:
        bool: クリーンアップ成功ならTrue、失敗ならFalse
    """
    print("🧹 Redisデータボリュームをクリーンアップしています...")
    try:
        # コンテナを停止
        run_command("docker-compose down", capture_output=False)
        
        # 全ボリュームをリストし、Pythonでフィルタリング (クロスプラットフォーム対応)
        all_volumes = run_command("docker volume ls --format \"{{.Name}}\"")
        redis_volumes = []
        
        if all_volumes:
            # 文字列として返されたボリューム名をリストに分割し、redis-dataを含むものだけフィルタリング
            redis_volumes = [vol.strip() for vol in all_volumes.split('\n') if vol.strip() and 'redis-data' in vol]
        
        if not redis_volumes:
            print("📂 クリーンアップするRedisボリュームが見つかりませんでした")
            return True
        
        # 見つかったRedisボリュームを削除
        for volume in redis_volumes:
            print(f"🗑️ ボリューム {volume} を削除しています...")
            run_command(f"docker volume rm {volume}", capture_output=False)
        
        print("✅ Redisデータボリュームをクリーンアップしました")
        return True
    except Exception as e:
        print(f"❌ Redisボリュームのクリーンアップに失敗しました: {e}")
        return False

def force_reset_redis():
    """
    Redisデータを強制的にリセットする関数
    一時的にバインドマウントを使い、データディレクトリを空にしてからボリュームに戻す
    
    Returns:
        bool: リセット成功ならTrue、失敗ならFalse
    """
    print("🔄 Redisデータを強制的にリセットしています...")
    try:
        # 一時ディレクトリ名
        temp_dir = "temp_redis_data"
        
        # コンテナを停止
        run_command("docker-compose down", capture_output=False)
        
        # 一時ディレクトリを作成
        if not os.path.exists(temp_dir):
            os.makedirs(temp_dir)
        
        # 一時的な設定ファイルを作成
        temp_compose = """services:
  redis:
    image: redis:7-alpine
    volumes:
      - ./temp_redis_data:/data
    command: redis-server --appendonly no --save ""
    ports:
      - "6379:6379"
"""
        with open("temp_compose.yml", "w") as f:
            f.write(temp_compose)
        
        # 一時的なRedisコンテナを起動して即座に停止（データディレクトリ初期化のため）
        print("📂 新しいRedisデータディレクトリを初期化しています...")
        run_command("docker-compose -f temp_compose.yml up -d redis", capture_output=False)
        time.sleep(3)  # 起動を少し待つ
        run_command("docker-compose -f temp_compose.yml down", capture_output=False)
        
        # 一時ファイルを削除
        if os.path.exists("temp_compose.yml"):
            os.remove("temp_compose.yml")
        
        # 元のDockerボリュームを削除する試み
        cleanup_redis_volume()
        
        print("✅ Redisデータを強制的にリセットしました")
        return True
    except Exception as e:
        print(f"❌ Redisデータのリセットに失敗しました: {e}")
        return False

def start_docker_services(use_bind_mount=False):
    """
    Dockerサービスを起動
    
    Args:
        use_bind_mount (bool): Trueの場合、バインドマウントを使用
        
    Returns:
        bool: 起動成功時はTrue、失敗時はFalse
    """
    print("🚀 Dockerサービスを起動しています...")
    compose_file = "docker-compose-bind.yml" if use_bind_mount else "docker-compose.yml"
    
    try:
        # まず古いコンテナがあれば停止・削除
        run_command("docker-compose down", capture_output=False)
        print("🧹 古いコンテナをクリーンアップしました")
        
        if use_bind_mount:
            # バインドマウント用のdocker-compose-bind.ymlを生成
            create_bind_mount_compose_file()
            print("📄 バインドマウント用の設定ファイルを生成しました")
            
        # 次にイメージをプル
        run_command(f"docker-compose -f {compose_file} pull", capture_output=False)
        print("📥 Dockerイメージを更新しました")
        
        # サービスを起動
        run_command(f"docker-compose -f {compose_file} up -d", capture_output=False)
        print("✅ Dockerサービスを起動しました")
        
        # サービスの状態を確認
        services_status = run_command(f"docker-compose -f {compose_file} ps", capture_output=True)
        print("\n📊 サービスの状態:")
        print(services_status)
        
        # Redisのログを特に確認
        print("\n🔍 Redisのログを確認:")
        redis_logs = run_command(f"docker-compose -f {compose_file} logs redis", capture_output=True)
        print(redis_logs if redis_logs else "Redisのログがありません")
        
        return True
    except Exception as e:
        print(f"❌ Dockerサービスの起動に失敗しました: {e}")
        
        # 詳細なエラー情報を取得
        try:
            docker_logs = run_command(f"docker-compose -f {compose_file} logs", capture_output=True)
            print("\n📋 Docker Composeのログ:")
            print(docker_logs if docker_logs else "ログが取得できませんでした")
        except:
            pass
        
        return False

def get_ngrok_url():
    """ngrokのパブリックURLを取得"""
    print("🔍 ngrokのパブリックURLを取得中...")
    
    # 最大10回、5秒おきに試行
    for attempt in range(10):
        try:
            import requests
            response = requests.get("http://localhost:4040/api/tunnels")
            data = response.json()
            
            # HTTPSのURLを取得
            https_url = next(
                (tunnel['public_url'] for tunnel in data.get('tunnels', []) 
                 if tunnel.get('proto') == 'https'),
                None
            )
            
            if https_url:
                print(f"✅ ngrok URL: {https_url}")
                return https_url
        except Exception as e:
            print(f"⏳ URLの取得に失敗（{attempt + 1}/10回目）: {e}")
        
        time.sleep(5)
    
    print("❌ ngrokのURLを取得できませんでした")
    return None

def update_env_file(url):
    """環境変数ファイルを更新"""
    print("✏️ .envファイルを更新しています...")
    
    try:
        with open(".env", "r") as f:
            content = f.read()
        
        # URLを更新または追加
        updates = {
            "FRONTEND_URL": url,
            "GOOGLE_REDIRECT_URI": f"{url}/oauth/google/callback",
            "BACKEND_URL": url
        }
        
        for key, value in updates.items():
            if re.search(f"^{key}=", content, re.MULTILINE):
                # 既存の行を置換
                content = re.sub(
                    f"^{key}=.*$", 
                    f"{key}={value}", 
                    content, 
                    flags=re.MULTILINE
                )
            else:
                # 新しい行を追加
                content += f"\n{key}={value}\n"
        
        with open(".env", "w") as f:
            f.write(content)
        
        print("✅ .envファイルを更新しました")
        return True
    except Exception as e:
        print(f"❌ .envファイルの更新に失敗しました: {e}")
        return False

def start_desktop_client():
    """デスクトップクライアントを起動"""
    desktop_client_path = "client/desktop-client.py"
    
    if not os.path.exists(desktop_client_path):
        print("⚠️ デスクトップクライアントが見つかりません")
        return False
    
    try:
        # 仮想環境内のPythonを使用（存在する場合）
        python_path = sys.executable
        
        # デスクトップクライアントを別プロセスで起動
        subprocess.Popen([python_path, desktop_client_path], 
                         start_new_session=True)
        
        print("✅ デスクトップクライアントを起動しました")
        return True
    except Exception as e:
        print(f"❌ デスクトップクライアントの起動に失敗しました: {e}")
        return False

def display_urls(url):
    """システム情報と接続URLを表示"""
    print("\n🎉 スマート予定管理システムの起動が完了しました！\n")
    
    # Slack関連URL
    print("Slack API設定用URL:")
    print("-------------------------------------------")
    print(f"イベントサブスクリプション: {url}/webhook/slack/events")
    print(f"スラッシュコマンド:         {url}/webhook/slack/command")
    print(f"インタラクティブコンポーネント: {url}/webhook/slack/interactive")
    print(f"Google OAuth リダイレクト: {url}/oauth/google/callback")
    print("-------------------------------------------\n")
    
    # 各サービスURL
    services = {
        "バックエンドサーバー": url,
        "通知センター": f"{url}:5002",
        "デスクトップ通知サーバー": "http://localhost:5010"
    }
    
    print("サービスURL:")
    print("-------------------------------------------")
    for name, service_url in services.items():
        print(f"{name}: {service_url}")
    print("-------------------------------------------\n")
    
    # デスクトップクライアントと通知センターを自動オープン
    try:
        webbrowser.open(f"{url}:5002")
        print("✅ ブラウザで通知センターを開きました")
    except Exception as e:
        print(f"⚠️ 通知センターのブラウザオープンに失敗: {e}")

def check_redis_status(max_retry=5, retry_interval=5):
    """
    Redisサーバーの状態を確認する
    
    Args:
        max_retry (int): 最大再試行回数
        retry_interval (int): 再試行間隔（秒）
        
    Returns:
        bool: Redisが正常に動作していればTrue、それ以外はFalse
    """
    print("🔍 Redisサーバーの状態を確認しています...")
    
    for attempt in range(max_retry):
        try:
            # Redisコンテナの状態を確認
            status = run_command("docker-compose ps redis")
            if "Up" in status:
                # 接続テスト用の簡易スクリプト
                test_script = "import redis; r = redis.from_url('redis://localhost:6379/0'); print(r.ping())"
                result = run_command(f'python -c "{test_script}"', capture_output=True)
                
                if result and "True" in result:
                    print("✅ Redisサーバーが正常に動作しています")
                    return True
                else:
                    print(f"⚠️ Redisサーバーは起動していますが、接続できません。再試行: {attempt + 1}/{max_retry}")
            else:
                print(f"⚠️ Redisサーバーが起動していません。再試行: {attempt + 1}/{max_retry}")
                
            # Redisのログを確認
            redis_logs = run_command("docker-compose logs redis", capture_output=True)
            print(f"📋 Redisログ:\n{redis_logs[:500]}...")
            
            if attempt < max_retry - 1:
                print(f"⏳ {retry_interval}秒後に再確認します...")
                time.sleep(retry_interval)
                
        except Exception as e:
            print(f"❌ Redisの状態確認中にエラーが発生しました: {e}")
            if attempt < max_retry - 1:
                time.sleep(retry_interval)
    
    print("❌ Redisサーバーの動作確認に失敗しました")
    return False

def main():
    """メインプログラム"""
    print("🚀 スマート予定管理システム 統合起動スクリプト\n")
    
    # 依存関係のチェック
    if not check_dependencies():
        print("依存関係の確認に失敗しました。インストールを確認してください。")
        return
    
    # 環境設定ファイルの確認
    if not check_and_create_env_file():
        print("環境設定ファイルの準備に失敗しました。")
        return
    
    # 起動方法を選択
    print("\n起動オプションを選択してください:")
    print("1. 通常起動")
    print("2. バインドマウントを使用して起動 (Redis問題の解決に有効)")
    print("3. Redisデータをクリーンアップして起動 (データ損失注意)")
    print("4. Redisデータを強制リセットして起動 (最終手段・データ損失注意)")
    
    choice = input("選択 (1-4、デフォルト=1): ").strip() or "1"
    
    use_bind_mount = choice == "2"
    clean_redis = choice == "3"
    force_reset = choice == "4"
    
    # Redis強制リセット
    if force_reset:
        if not force_reset_redis():
            print("Redisデータの強制リセットに失敗しました。")
            retry = input("それでも続行しますか？ (y/n): ").strip().lower()
            if retry not in ['y', 'yes']:
                return
    # Redisボリュームのクリーンアップ
    elif clean_redis:
        if not cleanup_redis_volume():
            print("Redisボリュームのクリーンアップに失敗しました。")
            retry = input("それでも続行しますか？ (y/n): ").strip().lower()
            if retry not in ['y', 'yes']:
                return
    
    # Dockerサービスの起動
    if not start_docker_services(use_bind_mount):
        print("Dockerサービスの起動に失敗しました。")
        print("\n問題解決のオプション:")
        print("1. バインドマウントを使用して再試行")
        print("2. Redisデータをクリーンアップして再試行")
        print("3. Redisデータを強制リセットして再試行 (最終手段)")
        print("4. 終了")
        
        retry_option = input("選択 (1-4): ").strip()
        if retry_option == "1":
            if not start_docker_services(True):
                print("バインドマウントを使用しても起動に失敗しました。")
                return
        elif retry_option == "2":
            if not cleanup_redis_volume() or not start_docker_services(False):
                print("クリーンアップ後も起動に失敗しました。")
                return
        elif retry_option == "3":
            if not force_reset_redis() or not start_docker_services(False):
                print("強制リセット後も起動に失敗しました。")
                return
        else:
            return
    
    # Redisの状態確認
    if not check_redis_status():
        print("⚠️ Redisサーバーの状態が異常です。一部機能が制限されます。")
        continue_anyway = input("それでも続行しますか？ (y/n): ").strip().lower()
        if continue_anyway not in ['y', 'yes']:
            print("起動を中止します。")
            run_command("docker-compose down", capture_output=False)
            return
    
    # ngrokのURLを取得
    ngrok_url = get_ngrok_url()
    if not ngrok_url:
        print("ngrokのURLを取得できませんでした。")
        return
    
    # 環境変数ファイルの更新
    if not update_env_file(ngrok_url):
        print("環境変数ファイルの更新に失敗しました。")
        return
    
    # URLの表示
    display_urls(ngrok_url)
    
    # デスクトップクライアントの起動確認
    use_desktop = input("デスクトップクライアントを起動しますか？ (y/n): ").strip().lower()
    if use_desktop in ['y', 'yes', '']:
        start_desktop_client()
    
    print("\n✨ スマート予定管理システムの起動が完了しました。")
    print("ヒント: docker-compose logs -f でサービスのログを確認できます。\n")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n🛑 起動プロセスを中断しました。")
    except Exception as e:
        print(f"❌ 予期しないエラーが発生しました: {e}")