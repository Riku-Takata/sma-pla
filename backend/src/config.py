"""
アプリケーション全体の設定ファイル
環境変数から設定を読み込み、変換します
"""
import os
from dotenv import load_dotenv

# .envファイルの読み込み（存在する場合）
load_dotenv()

class Config:
    """アプリケーション設定クラス"""
    
    # アプリケーション設定
    DEBUG = os.getenv("DEBUG", "False").lower() in ["true", "1", "t", "yes", "y"]
    TESTING = os.getenv("TESTING", "False").lower() in ["true", "1", "t", "yes", "y"]
    SECRET_KEY = os.getenv("SECRET_KEY", os.urandom(24).hex())
    
    # データベース設定
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "sqlite:///smart_schedule.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Redis設定
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    NOTIFICATION_CHANNEL = os.getenv("NOTIFICATION_CHANNEL", "smart_scheduler_notifications")
    
    # フロントエンド設定
    FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5002")
    
    # Slack設定
    SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
    SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")
    
    # Google認証設定
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "")
    
    # OpenAI設定（オプション - 自然言語処理の拡張用）
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    
    # ログ設定
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    @classmethod
    def get_google_client_config(cls):
        """Google OAuth用のクライアント設定を取得"""
        return {
            "web": {
                "client_id": cls.GOOGLE_CLIENT_ID,
                "client_secret": cls.GOOGLE_CLIENT_SECRET,
                "redirect_uris": [cls.GOOGLE_REDIRECT_URI],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token"
            }
        }

class DevelopmentConfig(Config):
    """開発環境用設定"""
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "sqlite:///dev_smart_schedule.db")

class TestingConfig(Config):
    """テスト環境用設定"""
    TESTING = True
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"

class ProductionConfig(Config):
    """本番環境用設定"""
    DEBUG = False
    
    # 本番環境では必ずSECRET_KEYを設定すること
    SECRET_KEY = os.getenv("SECRET_KEY")
    
    # 本番環境ではSQLiteではなくPostgreSQLなどを使用
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL")
    
    # 本番用ログ設定
    LOG_LEVEL = os.getenv("LOG_LEVEL", "ERROR")

# 環境に基づいて設定を選択
config_by_name = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig
}

# デフォルト設定
Config = config_by_name.get(os.getenv("FLASK_ENV", "development"), DevelopmentConfig)