import os
import pickle
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import pytz
from googleapiclient.errors import HttpError
from models import User, db

# スコープを設定
SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/calendar.events'
]

# トークンの保存先
TOKEN_PATH = "token.pickle"
CREDENTIALS_FILE = "client_secret.json"

def create_oauth_flow():
    """デスクトップアプリ用のOAuth認証フローを作成する"""
    flow = InstalledAppFlow.from_client_secrets_file(
        CREDENTIALS_FILE, 
        scopes=SCOPES
    )
    # OOBフロー用のリダイレクトURI
    flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
    return flow

def get_authorization_url():
    """
    OAuth認証URLを生成する
    
    Returns:
        tuple: (auth_url, state)
    """
    flow = create_oauth_flow()
    auth_url, state = flow.authorization_url(
        access_type='offline',
        prompt='consent'
    )
    return auth_url, state

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
        
        # トークンをファイルに保存（単一ユーザー想定）
        with open(TOKEN_PATH, 'wb') as token:
            pickle.dump(credentials, token)
        
        # 呼び出し側が必要なトークンを辞書にまとめる
        token_data = {
            "refresh_token": credentials.refresh_token,
            "access_token": credentials.token,
        }
        return True, token_data

    except Exception as e:
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
    from models import User
    user = User.query.get(user_id)
    
    if not user or not user.google_refresh_token:
        return None, "Googleアカウントと連携されていません。認証を行ってください。"
    
    # token.pickleが存在する場合、そこから読み込む
    pickle_valid = False
    if os.path.exists(TOKEN_PATH):
        try:
            with open(TOKEN_PATH, 'rb') as token:
                credentials = pickle.load(token)
            
            if credentials and credentials.valid:
                pickle_valid = True
                return credentials, None
            
            # トークン期限切れの場合、リフレッシュトークンで更新
            if credentials and credentials.expired and credentials.refresh_token:
                try:
                    credentials.refresh(Request())
                    
                    # 更新されたトークンを再度保存
                    with open(TOKEN_PATH, 'wb') as token_file:
                        pickle.dump(credentials, token_file)
                    
                    pickle_valid = True
                    return credentials, None
                except Exception as e:
                    # リフレッシュに失敗した場合、次のステップでDBから再構築
                    print(f"Token refresh failed: {e}")
        except Exception as e:
            print(f"Error loading token.pickle: {e}")
            # ファイルの読み込みに失敗した場合は、DBから再構築
    
    if not pickle_valid:
        # token.pickleがない、無効、または更新に失敗した場合
        try:
            # DBからの認証情報で新しいCredentialsオブジェクトを作成
            creds = Credentials(
                token=user.google_access_token,
                refresh_token=user.google_refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=os.getenv("GOOGLE_CLIENT_ID"),
                client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
                scopes=SCOPES
            )
            
            # トークンが期限切れなら更新
            if creds.expired:
                try:
                    creds.refresh(Request())
                    
                    # 更新後のトークンをDBに保存
                    user.google_access_token = creds.token
                    from db import db
                    db.session.commit()
                    
                    # token.pickleにも保存
                    with open(TOKEN_PATH, 'wb') as token_file:
                        pickle.dump(creds, token_file)
                except Exception as e:
                    # リフレッシュに失敗した場合、エラーを返す
                    return None, f"トークンの更新に失敗しました: {str(e)}"
            
            return creds, None
        except Exception as e:
            # DBトークンからのCredentials作成に失敗した場合
            return None, f"認証情報の構築に失敗しました: {str(e)}"
    
    # ここに到達することはないはずだが、念のために
    return None, "予期せぬエラーが発生しました。再度認証を行ってください。"

def get_calendar_service(user_id):
    """
    Google Calendar APIサービスを取得する
    
    Returns:
        (service, error)
    """
    credentials, error = get_credentials_from_user(user_id)
    if error:
        return None, error
    
    try:
        service = build('calendar', 'v3', credentials=credentials)
        return service, None
    except Exception as e:
        return None, f"Google Calendar APIの初期化に失敗しました: {str(e)}"

def create_calendar_event(user_id, schedule_info):
    """
    カレンダーに予定を登録する
    
    Returns:
        (success, result):
            success: True/False
            result: 成功時はイベントURL, 失敗時はエラーメッセージ
    """
    service, error = get_calendar_service(user_id)
    if error:
        return False, error
    
    # タイムゾーンをJSTに統一
    if schedule_info['start_datetime'].tzinfo is None:
        tz = pytz.timezone('Asia/Tokyo')
        schedule_info['start_datetime'] = tz.localize(schedule_info['start_datetime'])
    if schedule_info['end_datetime'].tzinfo is None:
        tz = pytz.timezone('Asia/Tokyo')
        schedule_info['end_datetime'] = tz.localize(schedule_info['end_datetime'])
    
    event_body = {
        'summary': schedule_info['title'],
        'location': schedule_info.get('location', ''),
        'description': schedule_info.get('description', ''),
        'start': {
            'dateTime': schedule_info['start_datetime'].isoformat(),
            'timeZone': 'Asia/Tokyo',
        },
        'end': {
            'dateTime': schedule_info['end_datetime'].isoformat(),
            'timeZone': 'Asia/Tokyo',
        },
        'reminders': {
            'useDefault': True,
        },
    }
    
    try:
        created_event = service.events().insert(calendarId='primary', body=event_body).execute()
        return True, created_event['htmlLink']
    except HttpError as e:
        return False, f"予定の登録に失敗しました: {str(e)}"
    except Exception as e:
        return False, f"予期しないエラーが発生しました: {str(e)}"

def check_schedule_conflicts(user_id, start_datetime, end_datetime):
    """
    予定の重複をチェックする
    """
    service, error = get_calendar_service(user_id)
    if error:
        return error, []
    
    # タイムゾーンの確認
    if start_datetime.tzinfo is None:
        tz = pytz.timezone('Asia/Tokyo')
        start_datetime = tz.localize(start_datetime)
    if end_datetime.tzinfo is None:
        tz = pytz.timezone('Asia/Tokyo')
        end_datetime = tz.localize(end_datetime)
    
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
            conflicts.append({
                'summary': event.get('summary', '（無題の予定）'),
                'start': start,
                'end': end,
                'location': event.get('location', ''),
                'html_link': event.get('htmlLink', '')
            })
        
        return True, conflicts
    except Exception as e:
        return str(e), []

def find_next_available_time(user_id, start_time, duration_minutes, max_days=7):
    """
    次に空いている時間を探す
    """
    service, error = get_calendar_service(user_id)
    if error:
        print(f"Error getting calendar service: {error}")
        return None
    
    if start_time.tzinfo is None:
        start_time = pytz.timezone('Asia/Tokyo').localize(start_time)
    
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
            
            regular_events = [e for e in events if 'dateTime' in e.get('start', {})]
            
            if not regular_events:
                if (day_end - day_start).total_seconds() / 60 >= duration_minutes:
                    return day_start
                else:
                    current_day = (current_day + timedelta(days=1)).replace(hour=9, minute=0)
                    continue
            
            parsed_events = []
            for event in regular_events:
                start = parse_datetime(event['start'].get('dateTime'))
                end = parse_datetime(event['end'].get('dateTime'))
                parsed_events.append((start, end))
            
            parsed_events.sort(key=lambda x: x[0])
            
            if parsed_events and (parsed_events[0][0] - day_start).total_seconds() / 60 >= duration_minutes:
                return day_start
            
            current_time = day_start
            for (event_start, event_end) in parsed_events:
                if current_time < event_start:
                    if (event_start - current_time).total_seconds() / 60 >= duration_minutes:
                        return current_time
                current_time = max(current_time, event_end)
            
            if parsed_events and (day_end - parsed_events[-1][1]).total_seconds() / 60 >= duration_minutes:
                return parsed_events[-1][1]
            
            current_day = (current_day + timedelta(days=1)).replace(hour=9, minute=0)
        
        return None
    
    except Exception as e:
        print(f"Error finding next available time: {e}")
        return None

def parse_datetime(datetime_str):
    """ISO形式の日時文字列をdatetimeオブジェクトに変換する"""
    import datetime
    import pytz
    
    if datetime_str.endswith('Z'):
        dt = datetime.datetime.fromisoformat(datetime_str.replace('Z', '+00:00'))
        return dt.astimezone(pytz.timezone('Asia/Tokyo'))
    
    try:
        dt = datetime.datetime.fromisoformat(datetime_str)
        if dt.tzinfo is None:
            dt = pytz.timezone('Asia/Tokyo').localize(dt)
        return dt
    except:
        import dateutil.parser
        dt = dateutil.parser.parse(datetime_str)
        if dt.tzinfo is None:
            dt = pytz.timezone('Asia/Tokyo').localize(dt)
        return dt

def get_calendar_service(user_id):
    """
    Google Calendar APIサービスを取得する
    
    Args:
        user_id (int): ユーザーID
        
    Returns:
        (service or None, error or None): カレンダーサービスとエラーメッセージのタプル
    """
    credentials, error = get_credentials_from_user(user_id)
    if error:
        return None, error
    
    try:
        service = build('calendar', 'v3', credentials=credentials)
        return service, None
    except Exception as e:
        # Google APIの初期化に失敗した場合も、古いtoken.pickleを削除
        if os.path.exists(TOKEN_PATH):
            try:
                os.remove(TOKEN_PATH)
            except:
                pass
        return None, f"Google Calendar APIの初期化に失敗しました: {str(e)}"
    