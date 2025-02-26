# app.py
from flask import Flask
from config import Config
from db import db
from models import User, UserPlatformLink

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # DB初期化
    db.init_app(app)

    # テーブル作成
    with app.app_context():
        db.create_all()  # まだテーブルがない場合に作成

    @app.route("/")
    def index():
        return "Hello, this is Smart Schedule base!"

    return app

# Gunicorn用にappをトップレベルで定義
app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=app.config["DEBUG"])
