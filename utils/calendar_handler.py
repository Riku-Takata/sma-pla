import os
from datetime import datetime, timedelta
import pytz
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from models import User, db

# Google APIの設定
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5001")
OAUTH_REDIRECT_URI = f"{FRONTEND_URL}/oauth/google/callback"

# スコープ設定
SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/calendar.events',
]

def create_oauth_flow():
    """OAuth認証フローを作成する"""
    client_config = {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [OAUTH_REDIRECT_URI],
        }
    }
    
    return Flow.from_client_config(
        client_config, 
        scopes=SCOPES, 
        redirect_uri=OAUTH_REDIRECT_URI
    )

def get_authorization_url():
    """
    OAuth認証URLを生成する
    
    Returns:
        tuple: (auth_url, state)
    """
    flow = create_oauth_flow()
    auth_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent',
    )
    
    return auth_url, state

def exchange_code_for_token(code):
    """
    認証コードをトークンと交換する
    
    Args:
        code (str): 認証コード
        
    Returns:
        google.oauth2.credentials.Credentials: 認証情報
    """
    flow = create_oauth_flow()
    flow.fetch_token(code=code)
    return flow.credentials

def get_credentials_from_user(user_id):
    """
    ユーザーIDから認証情報を取得する
    
    Args:
        user_id (int): ユーザーID
        
    Returns:
        google.oauth2.credentials.Credentials or None: 認証情報またはNone
        str or None: エラーメッセージまたはNone
    """
    user = User.query.get(user_id)
    if not user:
        return None, "ユーザーが見つかりません"
    
    if not user.google_refresh_token:
        return None, "Googleアカウントと連携されていません"
    
    # 認証情報を作成
    credentials = Credentials(
        token=user.google_access_token,
        refresh_token=user.google_refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=SCOPES
    )
    
    # トークンの有効期限が切れていたら更新
    if user.google_token_expiry and user.google_token_expiry < datetime.utcnow():
        try:
            credentials.refresh(None)
            
            # DBにトークン情報を更新
            user.google_access_token = credentials.token
            user.google_token_expiry = datetime.utcnow() + timedelta(seconds=credentials.expires_in)
            db.session.commit()
        except Exception as e:
            return None, f"トークンの更新に失敗しました: {str(e)}"
    
    return credentials, None

def get_calendar_service(user_id):
    """
    Google Calendar APIサービスを取得する
    
    Args:
        user_id (int): ユーザーID
        
    Returns:
        tuple: (service, error)
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
    
    Args:
        user_id (int): ユーザーID
        schedule_info (dict): 予定情報
            {
                'title': '予定のタイトル',
                'start_datetime': datetime object,
                'end_datetime': datetime object,
                'location': '場所',
                'description': '説明'
            }
        
    Returns:
        tuple: (success, result)
            success: 登録成功したかどうか
            result: 成功時はイベントURL、失敗時はエラーメッセージ
    """
    service, error = get_calendar_service(user_id)
    if error:
        return False, error
    
    # タイムゾーンの設定
    if schedule_info['start_datetime'].tzinfo is None:
        import pytz
        tz = pytz.timezone('Asia/Tokyo')
        schedule_info['start_datetime'] = tz.localize(schedule_info['start_datetime'])
        schedule_info['end_datetime'] = tz.localize(schedule_info['end_datetime'])
    
    # イベントの作成
    event = {
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
        created_event = service.events().insert(calendarId='primary', body=event).execute()
        return True, created_event['htmlLink']
    except HttpError as e:
        return False, f"予定の登録に失敗しました: {str(e)}"
    except Exception as e:
        return False, f"予期しないエラーが発生しました: {str(e)}"

def check_schedule_conflicts(user_id, start_datetime, end_datetime):
    """
    予定の重複をチェックする
    
    Args:
        user_id (int): ユーザーID
        start_datetime (datetime): 開始日時
        end_datetime (datetime): 終了日時
        
    Returns:
        tuple: (has_conflict, conflicts)
            has_conflict: 重複があるかどうか
            conflicts: 重複する予定のリスト
    """
    service, error = get_calendar_service(user_id)
    if error:
        return error, []
    
    # タイムゾーンの確認と設定
    if start_datetime.tzinfo is None:
        import pytz
        tz = pytz.timezone('Asia/Tokyo')
        start_datetime = tz.localize(start_datetime)
        end_datetime = tz.localize(end_datetime)
    
    # UTCに変換
    utc_start = start_datetime.astimezone(pytz.UTC).isoformat()
    utc_end = end_datetime.astimezone(pytz.UTC).isoformat()
    
    try:
        # 指定期間のイベントを取得
        events_result = service.events().list(
            calendarId='primary',
            timeMin=utc_start,
            timeMax=utc_end,
            singleEvents=True
        ).execute()
        
        events = events_result.get('items', [])
        
        if not events:
            return False, []
        
        # 重複する予定のリスト
        conflicts = []
        
        for event in events:
            # 終日イベントかどうかをチェック
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
    
    Args:
        user_id (int): ユーザーID
        start_time (datetime): 希望開始時間
        duration_minutes (int): 予定の所要時間（分）
        max_days (int): 最大何日先まで検索するか
        
    Returns:
        datetime or None: 次に空いている時間、見つからなければNone
    """
    service, error = get_calendar_service(user_id)
    if error:
        print(f"Error getting calendar service: {error}")
        return None
    
    # タイムゾーンの確認と設定
    if start_time.tzinfo is None:
        import pytz
        start_time = pytz.timezone('Asia/Tokyo').localize(start_time)
    
    # 検索の開始・終了日時
    current_day = start_time.replace(hour=9, minute=0, second=0, microsecond=0)
    if current_day < start_time:
        current_day = start_time  # 現在時刻が9時以降なら現在時刻から
    
    end_search = start_time + timedelta(days=max_days)  # 最大で指定日数先まで検索
    
    try:
        while current_day < end_search:
            # 一日の勤務時間を9:00-18:00とする（カスタマイズ可能）
            day_start = current_day
            day_end = current_day.replace(hour=18, minute=0)
            
            # すでに18時以降なら翌日の9時から
            if day_start.hour >= 18:
                current_day = (current_day + timedelta(days=1)).replace(hour=9, minute=0)
                continue
            
            # 現在の日の予定を取得
            events_result = service.events().list(
                calendarId='primary',
                timeMin=day_start.isoformat(),
                timeMax=day_end.isoformat(),
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            events = events_result.get('items', [])
            
            # 終日イベントを確認
            all_day_events = [e for e in events if 'date' in e.get('start', {})]
            if all_day_events:
                # 終日イベントがある場合は次の日へ
                current_day = (current_day + timedelta(days=1)).replace(hour=9, minute=0)
                continue
            
            # 通常イベントを時間順にソート
            regular_events = [e for e in events if 'dateTime' in e.get('start', {})]
            
            # イベントが存在しない場合は現在時刻から予定を入れられる
            if not regular_events:
                # 終業時間まで十分な時間があるか確認
                if (day_end - day_start).total_seconds() / 60 >= duration_minutes:
                    return day_start
                else:
                    # 十分な時間がなければ翌日へ
                    current_day = (current_day + timedelta(days=1)).replace(hour=9, minute=0)
                    continue
            
            # イベントの開始・終了時刻をパース
            parsed_events = []
            for event in regular_events:
                start = parse_datetime(event['start'].get('dateTime'))
                end = parse_datetime(event['end'].get('dateTime'))
                parsed_events.append((start, end))
            
            # 時間順にソート
            parsed_events.sort(key=lambda x: x[0])
            
            # 現在時刻から最初のイベントまでに十分な空きがあるか
            if parsed_events and (parsed_events[0][0] - day_start).total_seconds() / 60 >= duration_minutes:
                return day_start
            
            # 各イベント間の空き時間をチェック
            current_time = day_start
            
            for i, (event_start, event_end) in enumerate(parsed_events):
                # 現在時間がイベント開始よりも前なら、その間に予定を入れられるか確認
                if current_time < event_start:
                    if (event_start - current_time).total_seconds() / 60 >= duration_minutes:
                        return current_time
                
                # 次のチェックはイベント終了後から
                current_time = max(current_time, event_end)
            
            # 最後のイベント後に十分な時間があるか
            if parsed_events and (day_end - parsed_events[-1][1]).total_seconds() / 60 >= duration_minutes:
                return parsed_events[-1][1]
            
            # この日に空きがなければ翌日へ
            current_day = (current_day + timedelta(days=1)).replace(hour=9, minute=0)
        
        # 指定日数以内に空きがなければNoneを返す
        return None
    
    except Exception as e:
        print(f"Error finding next available time: {e}")
        return None

def parse_datetime(datetime_str):
    """ISO形式の日時文字列をdatetimeオブジェクトに変換する"""
    import datetime
    import pytz
    
    # 'Z'がついている場合（UTC）
    if datetime_str.endswith('Z'):
        dt = datetime.datetime.fromisoformat(datetime_str.replace('Z', '+00:00'))
        return dt.astimezone(pytz.timezone('Asia/Tokyo'))
    
    try:
        # Python 3.7以降のfromisoformat
        dt = datetime.datetime.fromisoformat(datetime_str)
        if dt.tzinfo is None:
            # タイムゾーンがない場合はJSTと仮定
            dt = pytz.timezone('Asia/Tokyo').localize(dt)
        return dt
    except:
        # フォールバック：日時文字列をパース
        import dateutil.parser
        dt = dateutil.parser.parse(datetime_str)
        if dt.tzinfo is None:
            dt = pytz.timezone('Asia/Tokyo').localize(dt)
        return dt