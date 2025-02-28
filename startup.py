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
        print(f"エラー出力: {e.stderr}")
        return None

def check_dependencies():
    """必要な依存関係をチェック"""
    dependencies = {
        "docker": "docker --version",
        "docker-compose": "docker-compose --version",
        "ngrok": "ngrok version"
    }
    
    for dep, cmd in dependencies.items():
        result = run_command(cmd)
        if result is None:
            print(f"❌ {dep.capitalize()}が見つかりません。インストールしてください。")
            return False
        print(f"✅ {dep.capitalize()}: {result}")
    
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

def start_docker_services():
    """Dockerサービスを起動"""
    print("🚀 Dockerサービスを起動しています...")
    try:
        run_command("docker-compose up -d", capture_output=False)
        print("✅ Dockerサービスを起動しました")
        return True
    except Exception as e:
        print(f"❌ Dockerサービスの起動に失敗しました: {e}")
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
    
    # Dockerサービスの起動
    if not start_docker_services():
        print("Dockerサービスの起動に失敗しました。")
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