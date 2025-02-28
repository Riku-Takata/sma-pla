# models.py
from datetime import datetime
from archive.db import db
from sqlalchemy import func

class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Google Calendar連携用
    google_refresh_token = db.Column(db.String, nullable=True)
    google_access_token = db.Column(db.String, nullable=True)
    google_token_expiry = db.Column(db.DateTime, nullable=True)

    # ユーザー名やメールアドレスなど
    email = db.Column(db.String, nullable=True)
    display_name = db.Column(db.String, nullable=True)

    def __repr__(self):
        return f"<User id={self.id} email={self.email}>"


class UserPlatformLink(db.Model):
    __tablename__ = "user_platform_links"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    platform_name = db.Column(db.String, nullable=False)  # "slack", "line", "discord", "teams"
    platform_user_id = db.Column(db.String, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("platform_links", lazy=True))

    def __repr__(self):
        return f"<UserPlatformLink {self.platform_name} user_id={self.user_id}>"
