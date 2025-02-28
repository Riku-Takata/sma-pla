#!/usr/bin/env python3
"""
スマート予定管理ボット起動スクリプト
Windows, macOS, Linux全てで動作する代替スクリプト
"""

import os
import sys
import time
import json
import subprocess
import re
import platform


def run_command(command):
    """コマンドを実行して結果を返す"""
    try:
        result = subprocess.run(command, shell=True, check=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"コマンド実行エラー: {e}")
        print(f"エラー出力: {e.stderr}")
        return None


def check_docker_compose():
    """Docker Composeが利用可能かチェック"""
    command = "docker-compose --version"
    output = run_command(command)
    
    if not output:
        print("❌ Docker Composeが見つかりません。インストールしてください。")
        return False
    
    print(f"✅ Docker Compose: {output}")
    return True


def check_env_file():
    """環境変数ファイルをチェックして必要なら作成"""
    if not os.path.exists(".env"):
        print("⚠️ .envファイルが見つかりません。サンプルからコピーします。")
        
        if os.path.exists(".env.example"):
            with open(".env.example", "r") as src:
                with open(".env", "w") as dst:
                    dst.write(src.read())
            print("✅ .env.exampleを.envにコピーしました。")
        else:
            with open(".env", "w") as f:
                f.write("# 環境変数設定\n")
            print("✅ 空の.envファイルを作成しました。")
        
        print("⚠️ .envファイルを編集して必要な環境変数を設定してください。")
    
    return True


def start_services():
    """Docker Composeサービスを起動"""
    print("🚀 サービスを起動しています...")
    result = run_command("docker-compose up -d")
    
    if result is None:
        print("❌ サービスの起動に失敗しました。")
        return False
    
    print("✅ サービスを起動しました")
    return True


def get_ngrok_url():
    """ngrokのパブリックURLを取得"""
    print("⏳ ngrokの起動を待機しています...")
    time.sleep(5)  # ngrokが起動するまで待機
    
    print("🔍 ngrokのパブリックURLを取得中...")
    try:
        import requests
        response = requests.get("http://localhost:4040/api/tunnels")
        data = response.json()
    except Exception as e:
        try:
            # requestsがない場合はcurlを使用
            output = run_command("curl -s http://localhost:4040/api/tunnels")
            if not output:
                print("❌ ngrokのURLを取得できませんでした。")
                return None
            data = json.loads(output)
        except Exception as e2:
            print(f"❌ ngrokのURLを取得できませんでした: {e2}")
            return None
    
    # HTTPS URLを取得
    tunnels = data.get('tunnels', [])
    url = None
    
    for tunnel in tunnels:
        if tunnel.get('proto') == 'https':
            url = tunnel.get('public_url')
            break
    
    if not url:
        print("❌ ngrokのHTTPS URLが見つかりませんでした。")
        return None
    
    print(f"✅ ngrok URL: {url}")
    return url


def update_env_file(url):
    """環境変数ファイルを更新"""
    print("✏️ .envファイルにngrokのURLを設定しています...")
    try:
        # ファイル内容を読み込む
        with open(".env", "r") as f:
            content = f.read()
        
        # FRONTEND_URLの行を置換または追加
        if re.search(r"^FRONTEND_URL=", content, re.MULTILINE):
            content = re.sub(r"^FRONTEND_URL=.*$", f"FRONTEND_URL={url}", content, flags=re.MULTILINE)
        else:
            content += f"\nFRONTEND_URL={url}\n"
        
        # ファイルに書き戻す
        with open(".env", "w") as f:
            f.write(content)
        
        print("✅ .envファイルを更新しました")
        return True
    except Exception as e:
        print(f"❌ .envファイルの更新に失敗しました: {e}")
        return False


def display_urls(url):
    """設定に必要なURLを表示"""
    print("\n✅ サービスが起動しました！\n")
    print("📋 以下のURLをSlack API設定に使用してください:")
    print("----------------------------------------")
    print(f"Event Subscriptions URL: {url}/webhook/slack/events")
    print(f"Slash Commands URL:      {url}/webhook/slack/command")
    print(f"Interactive Components:  {url}/webhook/slack/interactive")
    print(f"OAuth Redirect URL:      {url}/oauth/google/callback")
    print("----------------------------------------")
    print("")
    print("✅ ログを確認するには: docker-compose logs -f")
    print("✅ 停止するには: docker-compose down")


def main():
    """メイン処理"""
    # OS確認
    os_name = platform.system()
    print(f"OSを検出: {os_name}\n")
    
    # 各ステップを実行
    if not check_docker_compose():
        return
    
    if not check_env_file():
        return
    
    if not start_services():
        return
    
    ngrok_url = get_ngrok_url()
    if not ngrok_url:
        return
    
    if not update_env_file(ngrok_url):
        return
    
    display_urls(ngrok_url)
    
    # Windows環境であれば入力待ち
    if os_name == "Windows":
        input("\n何かキーを押すと終了します...")


if __name__ == "__main__":
    main()