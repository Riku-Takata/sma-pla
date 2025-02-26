# config.py
import os
from dotenv import load_dotenv

# .envファイルの読み込み
load_dotenv()

class Config:
    # データベースのURL (SQLiteの場合は以下のようにファイルパスを指定)
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "sqlite:///my_smart_schedule.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Flaskのシークレットキー (CSRF対策などに使用)
    SECRET_KEY = os.getenv("SECRET_KEY", "change_this_in_production")

    # デバッグモード
    DEBUG = os.getenv("DEBUG", "False").lower() == "true"
