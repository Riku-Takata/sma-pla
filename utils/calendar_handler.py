# utils/calendar_handler.py
import os
import sys
import json
from datetime import datetime, timedelta

# プロジェクトのルートディレクトリをインポートパスに追加
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Google API関連のライブラリをインポート
try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import Flow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except ImportError:
    print("Google API関連のライブラリがインストールされていません。")
    print("pip install google-auth google-auth-oauthlib google-api-python-client を実行してください。")
    # デモモード用の空のクラス定義
    class Credentials:
        pass
    class Flow:
        @classmethod
        def from_client_config(cls, client_config, scopes):
            return cls()
        def authorization_url(self, **kwargs):
            return "https://example.com/auth", "state"
        def redirect_uri(self, uri):
            self.redirect_uri = uri
        def fetch_token(self, code):
            self.credentials = type('obj', (object,), {
                'token': 'dummy_token',
                'refresh_token': 'dummy_refresh_token',
                'expires_in': 3600
            })

from flask import url_for, session, redirect, request
from models import User, db

# Google API設定
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "")
SCOPES = ['https://www.googleapis.com/auth/calendar']

def create_oauth_flow():
    """Google OAuth2認証フローを作成する"""
    flow = Flow.from_client_config(
        client_config={
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uris": [GOOGLE_REDIRECT_URI],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=SCOPES
    )
    flow.redirect_uri = GOOGLE_REDIRECT_URI
    return flow

def get_authorization_url():
    """認証URLを生成する"""
    flow = create_oauth_flow()
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'  # 必ずリフレッシュトークンを取得するために同意画面を表示
    )
    return authorization_url, state

def exchange_code_for_token(code):
    """認証コードをトークンと交換する"""
    flow = create_oauth_flow()
    flow.fetch_token(code=code)
    return flow.credentials

def get_calendar_service(user_id):
    """ユーザーIDからGoogle Calendar APIサービスを取得する"""
    user = User.query.get(user_id)
    if not user or not user.google_access_token:
        return None, "User not found or not authorized with Google Calendar"
    
    # 認証情報を作成
    creds = Credentials(
        token=user.google_access_token,
        refresh_token=user.google_refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=SCOPES
    )
    
    # トークンが期限切れの場合、リフレッシュトークンを使用して更新
    if user.google_token_expiry and datetime.utcnow() >= user.google_token_expiry:
        if not creds.refresh_token:
            return None, "Refresh token not available, re-authorization required"
        
        try:
            # OAuth2のRequestオブジェクトをインポート
            from google.auth.transport.requests import Request
            creds.refresh(Request())
            
            # DBのトークン情報を更新
            user.google_access_token = creds.token
            user.google_token_expiry = datetime.utcnow() + timedelta(seconds=creds.expires_in)
            db.session.commit()
        except Exception as e:
            return None, f"Failed to refresh token: {str(e)}"
    
    # Calendar APIサービスを構築
    try:
        service = build('calendar', 'v3', credentials=creds)
        return service, None
    except Exception as e:
        return None, f"Failed to build calendar service: {str(e)}"

def create_calendar_event(user_id, event_data):
    """
    Google Calendarに予定を作成する
    
    Args:
        user_id (int): ユーザーID
        event_data (dict): 予定情報
            {
                'title': イベントタイトル,
                'start_datetime': 開始日時 (datetime with timezone),
                'end_datetime': 終了日時 (datetime with timezone),
                'location': 場所 (optional),
                'description': 説明 (optional)
            }
    
    Returns:
        tuple: (成功したかどうか, メッセージまたはイベントのリンク)
    """
    service, error = get_calendar_service(user_id)
    if error:
        return False, error
    
    # イベントデータの形式をGoogle Calendar APIに合わせる
    event = {
        'summary': event_data.get('title', 'New Event'),
        'location': event_data.get('location', ''),
        'description': event_data.get('description', ''),
        'start': {
            'dateTime': event_data['start_datetime'].isoformat(),
            'timeZone': event_data['start_datetime'].tzname(),
        },
        'end': {
            'dateTime': event_data['end_datetime'].isoformat(),
            'timeZone': event_data['end_datetime'].tzname(),
        },
    }
    
    try:
        # カレンダーに予定を追加
        created_event = service.events().insert(calendarId='primary', body=event).execute()
        
        # 作成された予定のリンクを返す
        html_link = created_event.get('htmlLink', '')
        return True, html_link
    except Exception as e:
        # HttpErrorをインポートできない場合に対応
        error_msg = str(e)
        if "HttpError" in error_msg:
            return False, f"Failed to create event: {error_msg}"
        else:
            return False, f"An error occurred: {error_msg}"

def check_schedule_conflicts(user_id, start_time, end_time):
    """
    指定された時間帯に予定の重複がないか確認する
    
    Args:
        user_id (int): ユーザーID
        start_time (datetime): 開始時間
        end_time (datetime): 終了時間
    
    Returns:
        tuple: (重複があるかどうか, 重複する予定のリストまたはエラーメッセージ)
    """
    service, error = get_calendar_service(user_id)
    if error:
        return None, error
    
    try:
        # 指定された時間帯の予定を検索
        events_result = service.events().list(
            calendarId='primary',
            timeMin=start_time.isoformat(),
            timeMax=end_time.isoformat(),
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        
        if not events:
            return False, []
        
        # 重複する予定の情報を返す
        conflicting_events = []
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            end = event['end'].get('dateTime', event['end'].get('date'))
            conflicting_events.append({
                'summary': event.get('summary', 'Untitled Event'),
                'start': start,
                'end': end,
                'id': event['id'],
                'htmlLink': event.get('htmlLink', '')
            })
        
        return True, conflicting_events
    except Exception as e:
        # HttpErrorをインポートできない場合に対応
        error_msg = str(e)
        if "HttpError" in error_msg:
            return None, f"Failed to check conflicts: {error_msg}"
        else:
            return None, f"An error occurred: {error_msg}"