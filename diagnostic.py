#!/usr/bin/env python3
"""
スマート予定管理システム Docker診断スクリプト (Windows対応版)
プロジェクト構造とDockerコンテナの状態を診断します
"""
import os
import sys
import subprocess
import json
import socket
from datetime import datetime

class Colors:
    """コンソール出力の色定義"""
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    BOLD = '\033[1m'
    END = '\033[0m'

def print_header(message):
    """ヘッダーメッセージを表示"""
    print(f"\n{Colors.BLUE}{Colors.BOLD}=== {message} ==={Colors.END}")

def print_success(message):
    """成功メッセージを表示"""
    print(f"{Colors.GREEN}✓ {message}{Colors.END}")

def print_warning(message):
    """警告メッセージを表示"""
    print(f"{Colors.YELLOW}! {message}{Colors.END}")

def print_error(message):
    """エラーメッセージを表示"""
    print(f"{Colors.RED}✗ {message}{Colors.END}")

def print_info(message):
    """情報メッセージを表示"""
    print(f"  {message}")

def run_command(command, show_output=False):
    """
    シェルコマンドを実行し、出力を返す
    
    Args:
        command (str): 実行するシェルコマンド
        show_output (bool): 出力を表示するかどうか
    
    Returns:
        str: コマンドの出力（成功時）
        None: 失敗時
    """
    try:
        result = subprocess.run(
            command, 
            shell=True, 
            check=False,  # エラーを例外として扱わない
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            text=True
        )
        
        if result.returncode != 0:
            if show_output:
                print_error(f"コマンドの実行に失敗しました: {command}")
                print_info(f"エラー: {result.stderr}")
            return None
        
        if show_output:
            print_info(result.stdout)
        
        return result.stdout.strip()
    except Exception as e:
        if show_output:
            print_error(f"例外が発生しました: {e}")
        return None

def check_docker_status():
    """Dockerの状態を確認"""
    print_header("Docker状態確認")
    
    # Dockerが実行中か確認
    docker_version = run_command("docker --version")
    if docker_version:
        print_success(f"Docker実行中: {docker_version}")
    else:
        print_error("Dockerが実行されていないか、インストールされていません")
        return False
    
    # Docker Composeが実行中か確認
    compose_version = run_command("docker-compose --version")
    if compose_version:
        print_success(f"Docker Compose実行中: {compose_version}")
    else:
        print_error("Docker Composeが実行されていないか、インストールされていません")
        return False
    
    return True

def check_project_structure():
    """プロジェクト構造を確認"""
    print_header("プロジェクト構造確認")
    
    # プロジェクトディレクトリの確認
    current_dir = os.getcwd()
    print_info(f"カレントディレクトリ: {current_dir}")
    
    # 必要なファイルが存在するか確認
    required_files = [
        "docker-compose.yml",
        "backend/Dockerfile",
        "frontend/Dockerfile",
        ".env",
    ]
    
    missing_files = []
    for file_path in required_files:
        if not os.path.exists(file_path):
            missing_files.append(file_path)
    
    if missing_files:
        print_error("以下の必須ファイルが見つかりません:")
        for file_path in missing_files:
            print_info(f"  - {file_path}")
    else:
        print_success("必須ファイルがすべて存在します")
    
    # バックエンドのアプリケーション構造を確認
    backend_app_file = None
    if os.path.exists("backend/app.py"):
        backend_app_file = "backend/app.py"
        print_success("backend/app.py が見つかりました")
    elif os.path.exists("backend/src/app.py"):
        backend_app_file = "backend/src/app.py"
        print_warning("backend/app.py ではなく backend/src/app.py が見つかりました")
    else:
        print_error("バックエンドのエントリーポイント app.py が見つかりません")
    
    # Dockerfileのエントリーポイント設定を確認
    if os.path.exists("backend/Dockerfile"):
        try:
            # UTF-8で明示的に開く
            with open("backend/Dockerfile", "r", encoding="utf-8") as f:
                dockerfile_content = f.read()
                
            if "CMD" in dockerfile_content:
                cmd_lines = [line for line in dockerfile_content.split("\n") if "CMD" in line]
                if cmd_lines:
                    cmd_line = cmd_lines[0]
                    print_info(f"Dockerfile CMD設定: {cmd_line}")
                    
                    if "src.app" in cmd_line and not os.path.exists("backend/src/app.py"):
                        print_error("Dockerfileは src.app を参照していますが、そのファイルは存在しません")
                    elif "app:app" in cmd_line and not os.path.exists("backend/app.py"):
                        print_error("Dockerfileは app:app を参照していますが、backend/app.py は存在しません")
                    else:
                        print_success("Dockerfileのエントリーポイント設定に問題はありません")
                else:
                    print_warning("DockerfileにCMD命令が見つかりません")
            else:
                print_warning("DockerfileにCMD命令が見つかりません")
        except UnicodeDecodeError:
            # UTF-8で開けない場合、他のエンコーディングを試す
            try:
                with open("backend/Dockerfile", "r", encoding="latin-1") as f:
                    dockerfile_content = f.read()
                    
                print_warning("Dockerfileが正しいエンコーディングで開けません (UTF-8以外を使用中)")
                if "CMD" in dockerfile_content:
                    print_info("CMDコマンドが含まれています (詳細は表示できません)")
                else:
                    print_warning("DockerfileにCMD命令が見つかりません")
            except Exception as e:
                print_error(f"Dockerfileの読み込みに失敗しました: {e}")
    
    # Redis接続URLの確認
    if os.path.exists(".env"):
        try:
            with open(".env", "r", encoding="utf-8") as f:
                env_content = f.read()
                
            redis_url_match = [line for line in env_content.split("\n") if "REDIS_URL" in line]
            if redis_url_match:
                print_info(f"Redis接続URL設定: {redis_url_match[0]}")
            else:
                print_warning(".envファイルにREDIS_URL設定が見つかりません")
        except UnicodeDecodeError:
            try:
                with open(".env", "r", encoding="latin-1") as f:
                    env_content = f.read()
                
                print_warning(".envファイルが正しいエンコーディングで開けません (UTF-8以外を使用中)")
                if "REDIS_URL" in env_content:
                    print_info("REDIS_URL設定を含んでいます (詳細は表示できません)")
                else:
                    print_warning(".envファイルにREDIS_URL設定が見つかりません")
            except Exception as e:
                print_error(f".envファイルの読み込みに失敗しました: {e}")

def check_container_status():
    """Dockerコンテナの状態を確認"""
    print_header("コンテナ状態確認")
    
    # 実行中のコンテナ一覧
    containers = run_command("docker-compose ps")
    if containers:
        print_info("実行中のコンテナ:")
        print_info(containers)
    else:
        print_warning("実行中のコンテナが見つかりません")
        
    # コンテナの詳細情報を取得
    containers_json = run_command('docker ps --format "{{json .}}"')
    if containers_json:
        # Windowsの場合、改行方法が異なる可能性があるため、複数行に対応
        containers_json = containers_json.replace('\r\n', '\n')
        container_lines = [line for line in containers_json.split('\n') if line.strip()]
        
        try:
            container_list = [json.loads(line) for line in container_lines]
            
            for container in container_list:
                # 各コンテナの状態を確認
                container_id = container.get("ID", "")
                container_name = container.get("Names", "")
                container_status = container.get("Status", "")
                
                print_info(f"\nコンテナ: {container_name} ({container_id})")
                print_info(f"状態: {container_status}")
                
                # プロジェクト関連のコンテナのみ詳細チェック
                if any(name in container_name.lower() for name in ["redis", "web", "notification", "ngrok"]):
                    # コンテナ内のプロセスを確認
                    processes = run_command(f"docker exec {container_id} ps aux")
                    if processes:
                        print_info("実行中のプロセス:")
                        for line in processes.split("\n")[:5]:  # 最初の5行だけ表示
                            print_info(f"  {line}")
                    
                    # コンテナのログを確認
                    logs = run_command(f"docker logs --tail 5 {container_id}")
                    if logs:
                        print_info("最新のログ:")
                        for line in logs.split("\n"):
                            print_info(f"  {line}")
        except json.JSONDecodeError as e:
            print_error(f"JSONデコードエラー: {e}")
            print_info("コンテナ情報の詳細表示をスキップします")
    
    # コンテナ状態を簡易表示
    print_info("\nコンテナ状態の簡易表示:")
    docker_ps = run_command("docker ps")
    if docker_ps:
        print_info(docker_ps)
    else:
        print_warning("実行中のコンテナがありません")

def check_network_connectivity():
    """ネットワーク接続状態を確認"""
    print_header("ネットワーク接続確認")
    
    # ホスト名とIPアドレスを表示
    hostname = socket.gethostname()
    print_info(f"ホスト名: {hostname}")
    
    try:
        host_ip = socket.gethostbyname(hostname)
        print_info(f"IPアドレス: {host_ip}")
    except:
        print_warning("IPアドレスの取得に失敗しました")
    
    # Dockerネットワークの確認
    networks = run_command("docker network ls")
    if networks:
        print_info("Dockerネットワーク一覧:")
        print_info(networks)
        
        # app-networkの詳細を確認
        app_network = run_command("docker network inspect app-network")
        if app_network:
            try:
                network_info = json.loads(app_network)
                if network_info and len(network_info) > 0:
                    containers = network_info[0].get("Containers", {})
                    if containers:
                        print_info("\napp-networkに接続されたコンテナ:")
                        for container_id, container_info in containers.items():
                            print_info(f"  {container_info.get('Name')}: {container_info.get('IPv4Address')}")
                    else:
                        print_warning("app-networkに接続されたコンテナが見つかりません")
            except:
                print_warning("app-networkの詳細取得に失敗しました")
        else:
            print_warning("app-networkが見つかりません")
    
    # Redisの接続確認
    redis_ping = run_command("docker-compose exec redis redis-cli ping")
    if redis_ping and redis_ping.strip() == "PONG":
        print_success("Redisサーバーが応答しています (PONG)")
    else:
        print_error("Redisサーバーが応答していません")
    
    # バックエンドサーバーの接続確認
    backend_health = run_command("curl -s http://localhost:5001/api/health")
    if backend_health:
        try:
            health_info = json.loads(backend_health)
            if health_info.get("status") == "healthy":
                print_success("バックエンドサーバーが正常に応答しています")
                print_info(f"バックエンドヘルスチェック: {health_info}")
            else:
                print_warning("バックエンドサーバーのステータスが'healthy'ではありません")
        except:
            print_warning("バックエンドサーバーのレスポンスをパースできません")
    else:
        print_error("バックエンドサーバーに接続できません")
    
    # フロントエンドサーバーの接続確認
    frontend_health = run_command("curl -s http://localhost:5002/health")
    if frontend_health:
        try:
            health_info = json.loads(frontend_health)
            print_success("フロントエンドサーバーが応答しています")
            print_info(f"フロントエンドヘルスチェック: {health_info}")
            
            # Redis接続状態を確認
            if health_info.get("redis_connected") is True:
                print_success("フロントエンドからRedisへの接続が成功しています")
            else:
                print_error("フロントエンドからRedisへの接続に失敗しています")
        except:
            print_warning("フロントエンドサーバーのレスポンスをパースできません")
    else:
        print_error("フロントエンドサーバーに接続できません")

def main():
    """メイン関数"""
    print(f"{Colors.BLUE}{Colors.BOLD}")
    print("===================================================")
    print("  スマート予定管理システム Docker診断スクリプト")
    print(f"  実行日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("===================================================")
    print(f"{Colors.END}")
    
    # Dockerの状態確認
    if not check_docker_status():
        print_error("Dockerの状態確認に失敗しました。残りの診断をスキップします。")
        return
    
    # プロジェクト構造の確認
    check_project_structure()
    
    # コンテナの状態確認
    check_container_status()
    
    # ネットワーク接続状態の確認
    check_network_connectivity()
    
    print(f"\n{Colors.BLUE}{Colors.BOLD}")
    print("===================================================")
    print("  診断が完了しました")
    print("===================================================")
    print(f"{Colors.END}")
    
    print("\n修正手順:")
    print(f"{Colors.YELLOW}1. PowerShellスクリプトを実行してプロジェクト構造の自動修正を行う{Colors.END}")
    print("   > .\\fix-project.ps1")
    print(f"{Colors.YELLOW}2. 生成されたrestart-fixed.ps1スクリプトを実行してコンテナを再起動{Colors.END}")
    print("   > .\\restart-fixed.ps1")
    print(f"{Colors.YELLOW}3. ログを確認して問題が解消されたか確認{Colors.END}")
    print("   > docker-compose logs -f")

if __name__ == "__main__":
    main()