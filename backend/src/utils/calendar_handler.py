"""
Googleカレンダーハンドラー
認証、トークン管理、カレンダーAPI操作を担当
"""
import os
import json
import logging
from datetime import datetime, timedelta
import pytz
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.models.user import User
from src.utils.db import db
from flask import current_app

# ロギング設定
logger = logging.getLogger(__name__)

# スコープを設定
SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/calendar.events'
]

def create_oauth_flow():
    """
    Google OAuth認証フローを作成する
    
    Returns:
        Flow: 認証フローオブジェクト
    """
    client_config = current_app.config.get('GOOGLE_CLIENT_CONFIG', None)
    redirect_uri = current_app.config.get('GOOGLE_REDIRECT_URI', None)
    
    if not client_config:
        # 設定から取得できない場合、環境変数から直接構築
        client_config = {
            "web": {
                "client_id": current_app.config.get('GOOGLE_CLIENT_ID'),
                "client_secret": current_app.config.get('GOOGLE_CLIENT_SECRET'),
                "redirect_uris": [redirect_uri] if redirect_uri else [],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token"
            }
        }
    
    # auth_uri, token_uri, client_idのチェック
    if (not client_config.get('web', {}).get('client_id') or 
        not client_config.get('web', {}).get('auth_uri') or 
        not client_config.get('web', {}).get('token_uri')):
        raise ValueError("Invalid client configuration - missing required fields")
    
    try:
        flow = Flow.from_client_config(
            client_config,
            scopes=SCOPES
        )
        
        # リダイレクトURIの設定
        if redirect_uri:
            flow.redirect_uri = redirect_uri
        elif flow.redirect_uri is None and len(client_config.get('web', {}).get('redirect_uris', [])) > 0:
            flow.redirect_uri = client_config['web']['redirect_uris'][0]
        else:
            # デフォルトでOOB（ブラウザ外認証）を使用
            flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
        
        return flow
    except Exception as e:
        logger.error(f"OAuth認証フロー作成エラー: {e}", exc_info=True)
        raise

def get_authorization_url():
    """
    OAuth認証URLを生成する
    
    Returns:
        tuple: (auth_url, state)
    """
    try:
        flow = create_oauth_flow()
        auth_url, state = flow.authorization_url(
            access_type='offline',
            prompt='consent',
            include_granted_scopes='true'
        )
        logger.debug(f"認証URL生成: {auth_url[:100]}...")
        return auth_url, state
    except Exception as e:
        logger.error(f"認証URL生成エラー: {e}", exc_info=True)
        raise

def exchange_code_for_token(code):
    """
    認証コードをトークンと交換する
    
    Args:
        code (str): 認証コード
        
    Returns:
        (bool, dict or str):
            - True, { "refresh_token": str, "access_token": str, ... }  (成功時)
            - False, "エラーメッセージ" (失敗時)
    """
    try:
        flow = create_oauth_flow()
        flow.fetch_token(code=code)
        credentials = flow.credentials
        
        # トークン情報を辞書にまとめる
        token_data = {
            "refresh_token": credentials.refresh_token,
            "access_token": credentials.token,
            "token_uri": credentials.token_uri,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "scopes": credentials.scopes,
            "expiry": credentials.expiry.isoformat() if credentials.expiry else None,
            "expires_in": (credentials.expiry - datetime.utcnow()).total_seconds() if credentials.expiry else 3600
        }
        
        logger.debug(f"トークン交換成功. リフレッシュトークンあり: {bool(credentials.refresh_token)}")
        return True, token_data

    except Exception as e:
        logger.error(f"トークン交換エラー: {e}", exc_info=True)
        return False, f"{e}"

def get_credentials_from_user(user_id):
    """
    ユーザーIDから認証情報を取得する
    
    Args:
        user_id (int): ユーザーID
        
    Returns:
        (Credentials or None, str or None):
            credentials: トークンが有効ならCredentials
            error: エラー文字列またはNone
    """
    # まずDBからユーザー情報を取得
    user = User.query.get(user_id)
    
    if not user or not user.google_refresh_token:
        logger.warning(f"ユーザー {user_id} にGoogleリフレッシュトークンがありません")
        return None, "Googleアカウントと連携されていません。認証を行ってください。"
    
    try:
        # Credentials オブジェクトを構築
        creds = Credentials(
            token=user.google_access_token,
            refresh_token=user.google_refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=current_app.config.get('GOOGLE_CLIENT_ID'),
            client_secret=current_app.config.get('GOOGLE_CLIENT_SECRET'),
            scopes=SCOPES
        )
        
        # トークンが期限切れなら更新
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                try:
                    # トークンを更新
                    creds.refresh(Request())
                    
                    # 更新後のトークンをDBに保存
                    user.google_access_token = creds.token
                    user.google_token_expiry = creds.expiry
                    db.session.commit()
                    logger.debug(f"ユーザー {user_id} のトークンを更新しました")
                except Exception as e:
                    # リフレッシュに失敗した場合、エラーを返す
                    logger.error(f"ユーザー {user_id} のトークン更新に失敗: {e}", exc_info=True)
                    return None, f"トークンの更新に失敗しました: {str(e)}"
            else:
                # refresh_tokenがない場合
                logger.error(f"ユーザー {user_id} にリフレッシュトークンがありません")
                return None, "リフレッシュトークンがありません。再度認証してください。"
        
        return creds, None
    except Exception as e:
        # 全般的なエラー
        logger.error(f"ユーザー {user_id} の認証情報取得エラー: {e}", exc_info=True)
        return None, f"認証情報の構築に失敗しました: {str(e)}"

def get_calendar_service(user_id):
    """
    Google Calendar APIサービスを取得する
    
    Args:
        user_id (int): ユーザーID
    
    Returns:
        (service, error): サービスオブジェクトとエラー文字列のタプル
    """
    credentials, error = get_credentials_from_user(user_id)
    if error:
        return None, error
    
    try:
        service = build('calendar', 'v3', credentials=credentials)
        return service, None
    except Exception as e:
        logger.error(f"ユーザー {user_id} のカレンダーサービス構築エラー: {e}", exc_info=True)
        return None, f"Google Calendar APIの初期化に失敗しました: {str(e)}"

def create_calendar_event(user_id, schedule_info, notify_user=True):
    """
    カレンダーに予定を登録する
    
    Args:
        user_id (int): ユーザーID
        schedule_info (dict): 予定情報
        notify_user (bool): ユーザーにメール通知するか
        
    Returns:
        (success, result):
            success: True/False
            result: 成功時はイベントURL, 失敗時はエラーメッセージ
    """
    # 最大3回のリトライを実施
    for attempt in range(3):
        try:
            service, error = get_calendar_service(user_id)
            if error:
                return False, error
            
            # タイムゾーンをJSTに統一
            jst = pytz.timezone('Asia/Tokyo')
            start_dt = schedule_info['start_datetime']
            end_dt = schedule_info['end_datetime']
            
            if start_dt.tzinfo is None:
                start_dt = jst.localize(start_dt)
            else:
                start_dt = start_dt.astimezone(jst)
            
            if end_dt.tzinfo is None:
                end_dt = jst.localize(end_dt)
            else:
                end_dt = end_dt.astimezone(jst)
            
            # 終日イベントかどうか
            is_all_day = schedule_info.get('is_all_day', False)
            
            # イベント作成
            event_body = {
                'summary': schedule_info['title'],
                'location': schedule_info.get('location', ''),
                'description': schedule_info.get('description', ''),
                'reminders': {
                    'useDefault': True,
                },
            }
            
            # 通知設定
            if not notify_user:
                event_body['reminders'] = {
                    'useDefault': False,
                    'overrides': []
                }
            
            if is_all_day:
                # 終日イベントの場合
                event_body['start'] = {
                    'date': start_dt.date().isoformat(),
                    'timeZone': 'Asia/Tokyo',
                }
                event_body['end'] = {
                    'date': (end_dt.date() + timedelta(days=1)).isoformat(),  # 終日イベントは終了日に+1日
                    'timeZone': 'Asia/Tokyo',
                }
            else:
                # 通常イベントの場合
                event_body['start'] = {
                    'dateTime': start_dt.isoformat(),
                    'timeZone': 'Asia/Tokyo',
                }
                event_body['end'] = {
                    'dateTime': end_dt.isoformat(),
                    'timeZone': 'Asia/Tokyo',
                }
            
            created_event = service.events().insert(calendarId='primary', body=event_body).execute()
            logger.info(f"カレンダーイベント作成: id={created_event['id']}, ユーザー={user_id}")
            return True, created_event['htmlLink']
            
        except HttpError as e:
            logger.error(f"Calendar API error for user {user_id} (attempt {attempt+1}/3): {e}")
            if attempt == 2:  # 最終リトライ
                return False, f"予定の登録に失敗しました: {str(e)}"
            continue  # リトライ
            
        except Exception as e:
            logger.error(f"ユーザー {user_id} のイベント作成で予期しないエラー: {e}", exc_info=True)
            return False, f"予期しないエラーが発生しました: {str(e)}"

def check_schedule_conflicts(user_id, start_datetime, end_datetime):
    """
    予定の重複をチェックする
    
    Args:
        user_id (int): ユーザーID
        start_datetime: 開始日時
        end_datetime: 終了日時
        
    Returns:
        (has_conflict, conflicts):
            has_conflict: 重複があるか、またはエラーメッセージ
            conflicts: 重複するイベントのリスト
    """
    service, error = get_calendar_service(user_id)
    if error:
        return error, []
    
    # タイムゾーンの確認
    jst = pytz.timezone('Asia/Tokyo')
    if start_datetime.tzinfo is None:
        start_datetime = jst.localize(start_datetime)
    if end_datetime.tzinfo is None:
        end_datetime = jst.localize(end_datetime)
    
    # UTCに変換
    utc_start = start_datetime.astimezone(pytz.UTC).isoformat()
    utc_end = end_datetime.astimezone(pytz.UTC).isoformat()
    
    try:
        events_result = service.events().list(
            calendarId='primary',
            timeMin=utc_start,
            timeMax=utc_end,
            singleEvents=True
        ).execute()
        events = events_result.get('items', [])
        
        if not events:
            return False, []
        
        conflicts = []
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            end = event['end'].get('dateTime', event['end'].get('date'))
            
            # 日時の解析
            if 'date' in event['start']:
                # 終日イベント
                is_all_day = True
                start_dt = event['start'].get('date')
                end_dt = event['end'].get('date')
            else:
                # 通常イベント
                is_all_day = False
                start_dt = datetime.fromisoformat(start.replace('Z', '+00:00')).astimezone(jst).strftime('%Y-%m-%d %H:%M')
                end_dt = datetime.fromisoformat(end.replace('Z', '+00:00')).astimezone(jst).strftime('%H:%M')
                
                if start_dt.split()[0] != end_dt.split()[0]:
                    end_dt = datetime.fromisoformat(end.replace('Z', '+00:00')).astimezone(jst).strftime('%Y-%m-%d %H:%M')
            
            conflicts.append({
                'summary': event.get('summary', '（無題の予定）'),
                'start': start_dt,
                'end': end_dt,
                'location': event.get('location', ''),
                'html_link': event.get('htmlLink', ''),
                'is_all_day': is_all_day
            })
        
        return True, conflicts
    except Exception as e:
        logger.error(f"ユーザー {user_id} の予定重複チェックでエラー: {e}", exc_info=True)
        return str(e), []

def find_next_available_time(user_id, start_time, duration_minutes, max_days=7):
    """
    次に空いている時間を探す
    
    Args:
        user_id (int): ユーザーID
        start_time: 開始日時の候補
        duration_minutes (int): 予定の長さ（分）
        max_days (int): 最大検索日数
        
    Returns:
        datetime or None: 空き時間が見つかった場合はその時間、見つからない場合はNone
    """
    service, error = get_calendar_service(user_id)
    if error:
        logger.error(f"カレンダーサービス取得エラー: {error}")
        return None
    
    jst = pytz.timezone('Asia/Tokyo')
    if start_time.tzinfo is None:
        start_time = jst.localize(start_time)
    
    current_day = start_time.replace(hour=9, minute=0, second=0, microsecond=0)
    if current_day < start_time:
        current_day = start_time
    end_search = start_time + timedelta(days=max_days)
    
    try:
        while current_day < end_search:
            day_start = current_day
            day_end = current_day.replace(hour=18, minute=0)
            
            if day_start.hour >= 18:
                current_day = (current_day + timedelta(days=1)).replace(hour=9, minute=0)
                continue
            
            # 営業時間(9:00-18:00)内のイベントを取得
            events_result = service.events().list(
                calendarId='primary',
                timeMin=day_start.isoformat(),
                timeMax=day_end.isoformat(),
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            events = events_result.get('items', [])
            
            # 終日イベント
            all_day_events = [e for e in events if 'date' in e.get('start', {})]
            if all_day_events:
                current_day = (current_day + timedelta(days=1)).replace(hour=9, minute=0)
                continue
            
            # 通常イベント
            regular_events = [e for e in events if 'dateTime' in e.get('start', {})]
            
            # イベントがなければ、その日の開始時間が使用可能
            if not regular_events:
                return day_start
            
            # イベントを時間順にソート
            parsed_events = []
            for event in regular_events:
                start = datetime.fromisoformat(event['start']['dateTime'].replace('Z', '+00:00')).astimezone(jst)
                end = datetime.fromisoformat(event['end']['dateTime'].replace('Z', '+00:00')).astimezone(jst)
                parsed_events.append((start, end))
            
            parsed_events.sort(key=lambda x: x[0])
            
            # 最初のイベントの前に時間があるかチェック
            if (parsed_events[0][0] - day_start).total_seconds() / 60 >= duration_minutes:
                return day_start
            
            # イベント間の空き時間をチェック
            current_time = day_start
            for event_start, event_end in parsed_events:
                if current_time < event_start and (event_start - current_time).total_seconds() / 60 >= duration_minutes:
                    return current_time
                current_time = max(current_time, event_end)
            
            # 最後のイベント後に時間があるかチェック
            if (day_end - current_time).total_seconds() / 60 >= duration_minutes:
                return current_time
            
            # 次の日へ
            current_day = (current_day + timedelta(days=1)).replace(hour=9, minute=0)
        
        # 見つからなかった場合は1週間後の同じ時間を提案
        return start_time + timedelta(days=7)
    
    except Exception as e:
        logger.error(f"ユーザー {user_id} の空き時間検索エラー: {e}", exc_info=True)
        return None