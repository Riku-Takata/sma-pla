# -*- coding: utf-8 -*-
"""calender.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1jMGe6l2kRKhDxjplO8l83Bgp0X9W0QNM
"""

# Make sure to run the following command in your terminal or Jupyter notebook cell:
# !pip install --upgrade google-api-python-client google-auth-httplib2 google-auth-oauthlib


import datetime
import os
import pickle
import pytz
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# タイムゾーンを指定
TZ = pytz.timezone("Asia/Tokyo")

# トークンの保存先
TOKEN_PATH = "token.pickle"
CREDENTIALS_FILE = "client_secret.json"

# スコープを設定
SCOPES = ['https://www.googleapis.com/auth/calendar']


def get_calendar_service():
    """Google カレンダー API の認証を処理"""
    creds = None

    if os.path.exists(TOKEN_PATH):
        with open(TOKEN_PATH, 'rb') as token:
            creds = pickle.load(token)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"🔄 トークンのリフレッシュに失敗: {e}")
                creds = None

        if not creds:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"

            auth_url, _ = flow.authorization_url(prompt='consent')
            print(f"\n🔗 以下のURLを開いて認証してください:\n{auth_url}")

            code = input("👉 認証後に表示されたコードを入力してください: ").strip()
            flow.fetch_token(code=code)
            creds = flow.credentials

        with open(TOKEN_PATH, 'wb') as token:
            pickle.dump(creds, token)

    return build('calendar', 'v3', credentials=creds)


def parse_event_time(event_time):
    """GoogleカレンダーAPIのイベント時刻を正しく解析（終日イベント対応）"""
    if 'dateTime' in event_time:
        return datetime.datetime.fromisoformat(event_time['dateTime']).astimezone(TZ)
    elif 'date' in event_time:
        return TZ.localize(datetime.datetime.strptime(event_time['date'], "%Y-%m-%d"))
    return None


def find_conflicting_events(service, start_time, end_time):
    """指定した時間帯に重複する予定を検索"""
    events_result = service.events().list(
        calendarId='primary',
        timeMin=start_time.astimezone(pytz.UTC).isoformat(),
        timeMax=end_time.astimezone(pytz.UTC).isoformat(),
        singleEvents=True,
        orderBy='startTime'
    ).execute()

    events = events_result.get('items', [])
    conflicts = []

    for event in events:
        event_start = parse_event_time(event['start'])
        event_end = parse_event_time(event['end'])
        event_title = event.get('summary', '（無題の予定）')

        # 予定が重複している場合
        if event_start and event_end and (
            (start_time < event_end and start_time >= event_start) or
            (end_time > event_start and end_time <= event_end) or
            (start_time <= event_start and end_time >= event_end)
        ):
            conflicts.append((event_title, event_start, event_end))

    return conflicts


def find_next_available_time(service, start_time, duration_minutes):
    """次に空いている時間を探す"""
    events_result = service.events().list(
        calendarId='primary',
        timeMin=start_time.astimezone(pytz.UTC).isoformat(),
        timeMax=(start_time + datetime.timedelta(days=1)).astimezone(pytz.UTC).isoformat(),
        singleEvents=True,
        orderBy='startTime'
    ).execute()

    events = events_result.get('items', [])

    if not events:
        return start_time

    current_time = start_time
    for event in events:
        event_start = parse_event_time(event['start'])
        event_end = parse_event_time(event['end'])

        if event_start and event_end:
            if current_time + datetime.timedelta(minutes=duration_minutes) <= event_start:
                return current_time
            current_time = event_end

    return current_time


def add_event(service, summary, location, description, start_time, end_time):
    """イベントをカレンダーに追加する"""
    print("\n📅 追加する予定の詳細:")
    print(f"  🏷 タイトル: {summary}")
    print(f"  📍 場所: {location}")
    print(f"  📝 説明: {description}")
    print(f"  ⏰ 開始: {start_time}")
    print(f"  ⏳ 終了: {end_time}")

    # 重複する予定を取得
    conflicts = find_conflicting_events(service, start_time, end_time)

    if conflicts:
        print("\n⚠ **この時間帯には以下の予定と重複しています！**")
        for title, c_start, c_end in conflicts:
            print(f"  📅 {title}（{c_start.strftime('%Y-%m-%d %H:%M')} 〜 {c_end.strftime('%H:%M')}）")

        suggested_time = find_next_available_time(service, start_time, (end_time - start_time).seconds // 60)
        print(f"\n🕒 代わりに {suggested_time.strftime('%Y-%m-%d %H:%M')} に予定を追加できます。")
        choice = input("この時間で予定を追加しますか？ (y/n): ").strip().lower()
        if choice != 'y':
            print("❌ 予定の追加をキャンセルしました。")
            return None
        else:
            start_time = suggested_time
            end_time = start_time + datetime.timedelta(minutes=(end_time - start_time).seconds // 60)

    else:
        choice = input("\nこの予定を追加しますか？ (y/n): ").strip().lower()
        if choice != 'y':
            print("❌ 予定の追加をキャンセルしました。")
            return None

    event = {
        'summary': summary,
        'location': location,
        'description': description,
        'start': {
            'dateTime': start_time.isoformat(),
            'timeZone': 'Asia/Tokyo',
        },
        'end': {
            'dateTime': end_time.isoformat(),
            'timeZone': 'Asia/Tokyo',
        },
        'reminders': {
            'useDefault': True,
        },
    }

    try:
        event = service.events().insert(calendarId='primary', body=event).execute()
        return event
    except HttpError as error:
        print(f'エラーが発生しました: {error}')
        return None


# メインプログラム
try:
    print("🔄 Google Calendarサービスに接続中...")
    service = get_calendar_service()

    print("✅ Google カレンダー API に接続しました！")

    date_str = input("📅 予定の日付を YYYY-MM-DD 形式で入力してください: ")
    time_str = input("⏰ 予定の開始時間を HH:MM 形式で入力してください: ")

    start = datetime.datetime.strptime(date_str + " " + time_str, "%Y-%m-%d %H:%M")
    start = TZ.localize(start)

    duration = int(input("⌛ 予定の長さ（分単位）を入力してください: "))
    end = start + datetime.timedelta(minutes=duration)

    event_summary = "会議"
    event_location = "オフィス"
    event_description = "重要な打ち合わせ"

    created_event = add_event(service, event_summary, event_location, event_description, start, end)

    if created_event:
        print(f"✅ イベントが作成されました: {created_event['htmlLink']}")

except Exception as e:
    print(f"❌ 予期しないエラーが発生しました: {e}")

