# app.py
from flask import Flask, render_template, send_from_directory
import os
from config import Config
from db import db
from models import User, UserPlatformLink
from handlers.slack_handler import slack_bp
from archive.line_handler import register_line_handler
from routes.oauth_routes import register_oauth_routes

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # 静的ファイル用のディレクトリを作成
    os.makedirs(os.path.join(app.root_path, 'static'), exist_ok=True)

    # DB初期化
    db.init_app(app)

    # テーブル作成
    with app.app_context():
        db.create_all()  # まだテーブルがない場合に作成

    # Slackハンドラーの登録
    app.register_blueprint(slack_bp)
    
    # LINEハンドラーの登録
    register_line_handler(app)
    
    # OAuth関連のルート登録
    register_oauth_routes(app)

    # 認証成功・失敗ページ用の静的ファイル
    @app.route('/static/<path:path>')
    def serve_static(path):
        return send_from_directory('static', path)

    # 認証成功ページ
    @app.route('/auth_success.html')
    def auth_success():
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <title>認証成功</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body { font-family: Arial, sans-serif; text-align: center; padding: 50px; }
                .success { color: green; font-size: 24px; margin-bottom: 20px; }
                .message { margin-bottom: 30px; }
                .close-button { 
                    background-color: #4CAF50;
                    border: none;
                    color: white;
                    padding: 15px 32px;
                    text-align: center;
                    text-decoration: none;
                    display: inline-block;
                    font-size: 16px;
                    margin: 4px 2px;
                    cursor: pointer;
                    border-radius: 4px;
                }
            </style>
        </head>
        <body>
            <div class="success">✓ 認証成功</div>
            <div class="message">
                Googleカレンダーとの連携が完了しました。<br>
                このページを閉じて、LINEに戻ってください。
            </div>
            <button class="close-button" onclick="window.close()">閉じる</button>
        </body>
        </html>
        """

    # 認証エラーページ
    @app.route('/auth_error.html')
    def auth_error():
        error = request.args.get('error', '不明なエラー')
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>認証エラー</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; }}
                .error {{ color: red; font-size: 24px; margin-bottom: 20px; }}
                .message {{ margin-bottom: 30px; }}
                .close-button {{ 
                    background-color: #f44336;
                    border: none;
                    color: white;
                    padding: 15px 32px;
                    text-align: center;
                    text-decoration: none;
                    display: inline-block;
                    font-size: 16px;
                    margin: 4px 2px;
                    cursor: pointer;
                    border-radius: 4px;
                }}
            </style>
        </head>
        <body>
            <div class="error">✗ 認証エラー</div>
            <div class="message">
                エラーが発生しました: {error}<br>
                LINEに戻って、もう一度お試しください。
            </div>
            <button class="close-button" onclick="window.close()">閉じる</button>
        </body>
        </html>
        """

    @app.route("/")
    def index():
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <title>スマート予定管理</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body { 
                    font-family: 'Helvetica Neue', Arial, sans-serif; 
                    line-height: 1.6;
                    color: #333;
                    max-width: 800px;
                    margin: 0 auto;
                    padding: 20px;
                }
                h1 { color: #2c3e50; }
                .card {
                    background: #f9f9f9;
                    border-radius: 8px;
                    padding: 20px;
                    margin-bottom: 20px;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                }
                .steps {
                    counter-reset: step-counter;
                    list-style-type: none;
                    padding-left: 0;
                }
                .steps li {
                    position: relative;
                    margin-bottom: 15px;
                    padding-left: 40px;
                }
                .steps li:before {
                    content: counter(step-counter);
                    counter-increment: step-counter;
                    position: absolute;
                    left: 0;
                    width: 30px;
                    height: 30px;
                    line-height: 30px;
                    background-color: #3498db;
                    color: white;
                    border-radius: 50%;
                    text-align: center;
                }
                .qr-code {
                    text-align: center;
                    margin: 30px 0;
                }
            </style>
        </head>
        <body>
            <h1>スマート予定管理</h1>
            
            <div class="card">
                <h2>サービス概要</h2>
                <p>LINEのメッセージから予定を自動的に検出し、Googleカレンダーに登録するサービスです。予定の入力の手間を省き、ダブルブッキングを防ぎます。</p>
            </div>
            
            <div class="card">
                <h2>使い方</h2>
                <ol class="steps">
                    <li>下記のQRコードから友だち追加してください</li>
                    <li>友人との会話で予定について話し合った後、「/plan」または「予定」とだけ入力してください</li>
                    <li>それまでの会話履歴から自動的に予定情報を抽出します<br>例: 「明日15時にオフィスで打ち合わせしましょう」→「了解です」→「/plan」</li>
                    <li>初回利用時は、Googleアカウントとの連携画面が表示されます</li>
                    <li>連携すると、自動的に予定がGoogleカレンダーに登録されます</li>
                </ol>
                
                <div class="qr-code">
                    <p>QRコード (LINE友だち追加用)</p>
                    <img src="/static/qr_code.png" alt="LINE QRコード" style="max-width: 200px;">
                    <p><small>※ QRコードは実際に発行したものに置き換えてください</small></p>
                </div>
            </div>
        </body>
        </html>
        """

    return app

# Gunicorn用にappをトップレベルで定義
app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=app.config["DEBUG"])