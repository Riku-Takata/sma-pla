"""
データベース接続とユーティリティ
SQLAlchemyインスタンスを提供します
"""
from flask_sqlalchemy import SQLAlchemy

# SQLAlchemyのインスタンスを作成
db = SQLAlchemy()