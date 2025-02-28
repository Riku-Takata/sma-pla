"""
ユーザーデータモデル
ユーザー情報とプラットフォームリンクを管理します
"""
from datetime import datetime
from src.utils.db import db

class User(db.Model):
    """
    ユーザーモデル
    Google認証情報とプロファイル情報を保持
    """
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Google Calendar連携用
    google_refresh_token = db.Column(db.String, nullable=True)
    google_access_token = db.Column(db.String, nullable=True)
    google_token_expiry = db.Column(db.DateTime, nullable=True)

    # ユーザー情報
    email = db.Column(db.String, nullable=True, index=True)
    display_name = db.Column(db.String, nullable=True)
    
    # プラットフォームリンク（逆参照）
    platform_links = db.relationship('UserPlatformLink', back_populates='user', lazy='dynamic')

    @property
    def is_google_authenticated(self):
        """Googleで認証済みかどうかを判定"""
        return bool(self.google_refresh_token)
    
    @property
    def is_token_expired(self):
        """アクセストークンが期限切れかどうかを判定"""
        if not self.google_token_expiry:
            return True
        return self.google_token_expiry < datetime.utcnow()
    
    def to_dict(self):
        """ユーザー情報を辞書形式で返す"""
        return {
            'id': self.id,
            'email': self.email,
            'display_name': self.display_name,
            'is_google_authenticated': self.is_google_authenticated,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
    
    def __repr__(self):
        return f"<User id={self.id} email={self.email}>"


class UserPlatformLink(db.Model):
    """
    ユーザープラットフォームリンクモデル
    各メッセージングプラットフォームのユーザーIDをUserモデルに紐づける
    """
    __tablename__ = "user_platform_links"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    platform_name = db.Column(db.String, nullable=False)  # "slack", "line", "discord", "teams"
    platform_user_id = db.Column(db.String, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # 追加のプラットフォーム固有情報（JSON形式で保存）
    platform_data = db.Column(db.Text, nullable=True)
    
    # バックリファレンス
    user = db.relationship("User", back_populates="platform_links")
    
    # 複合インデックス（プラットフォーム名とユーザーID）
    __table_args__ = (
        db.UniqueConstraint('platform_name', 'platform_user_id', name='_platform_user_uc'),
    )

    def to_dict(self):
        """リンク情報を辞書形式で返す"""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'platform_name': self.platform_name,
            'platform_user_id': self.platform_user_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
    
    def __repr__(self):
        return f"<UserPlatformLink {self.platform_name} user_id={self.user_id}>"