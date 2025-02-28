import os
import re
from datetime import datetime, timedelta
import pytz
import json
from utils.openai_analyzer import analyze_conversation_with_openai

# OpenAI APIを使用する場合の設定
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

def parse_user_input_for_scheduling(text):
    """
    ユーザー入力テキストから予定情報を抽出する
    
    Args:
        text (str): 解析するテキスト
        
    Returns:
        dict: 予定情報を含む辞書、または解析失敗時はNone
    """
    try:
        # OpenAI APIを使用した強化版解析を最初に試みる
        if OPENAI_API_KEY:
            schedule_info, confidence = analyze_conversation_with_openai(text)
            if confidence >= 0.4:
                return schedule_info
            # 信頼度が低い場合は従来の方法にフォールバック
        
        # OpenAI APIが使えない、または信頼度が低い場合は簡易パーサーで解析
        return simple_date_time_parser(text)
    
    except Exception as e:
        print(f"Parsing error: {e}")
        return None

def convert_to_standard_format(openai_result):
    """
    OpenAI APIの結果を標準形式に変換
    
    Args:
        openai_result (dict): OpenAI APIから返されたJSON
        
    Returns:
        dict: 標準形式の予定情報
    """
    tz = pytz.timezone('Asia/Tokyo')
    
    # 日付の解析
    try:
        date_str = openai_result.get('date')
        if date_str:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
        else:
            # 日付が取得できなければ今日
            date_obj = datetime.now(tz).date()
        
        # 時間の解析（開始時間）
        start_time_str = openai_result.get('start_time')
        if start_time_str:
            start_time = datetime.strptime(start_time_str, "%H:%M").time()
            start_datetime = datetime.combine(date_obj, start_time)
            start_datetime = tz.localize(start_datetime)
        else:
            # 時間が指定されていなければ現在時刻の次の30分区切り
            now = datetime.now(tz)
            if now.minute < 30:
                start_datetime = now.replace(minute=30, second=0, microsecond=0)
            else:
                start_datetime = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        
        # 終了時間
        end_time_str = openai_result.get('end_time')
        if end_time_str:
            end_time = datetime.strptime(end_time_str, "%H:%M").time()
            end_datetime = datetime.combine(date_obj, end_time)
            end_datetime = tz.localize(end_datetime)
            
            # 終了時間が開始時間より前の場合（例：23:00-1:00）は翌日と解釈
            if end_datetime <= start_datetime:
                end_datetime = end_datetime + timedelta(days=1)
        else:
            # 指定がなければ1時間後
            end_datetime = start_datetime + timedelta(hours=1)
        
        # 終日イベントの場合
        is_all_day = openai_result.get('all_day', False)
        if is_all_day:
            start_datetime = datetime.combine(date_obj, datetime.min.time())
            start_datetime = tz.localize(start_datetime)
            end_datetime = start_datetime + timedelta(days=1)
        
        return {
            'title': openai_result.get('title', '予定'),
            'start_datetime': start_datetime,
            'end_datetime': end_datetime,
            'location': openai_result.get('location', ''),
            'is_all_day': is_all_day,
            'confidence': float(openai_result.get('confidence', 0.7))
        }
    
    except Exception as e:
        print(f"Format conversion error: {e}")
        # 最低限の情報で辞書を返す
        now = datetime.now(tz)
        return {
            'title': openai_result.get('title', '予定'),
            'start_datetime': now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1),
            'end_datetime': now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=2),
            'location': '',
            'is_all_day': False,
            'confidence': 0.3
        }

def simple_date_time_parser(text):
    """
    シンプルな正規表現ベースの日時パーサー
    
    Args:
        text (str): 解析するテキスト
        
    Returns:
        dict: 予定情報を含む辞書
    """
    tz = pytz.timezone('Asia/Tokyo')
    now = datetime.now(tz)
    
    # 予定の初期値
    schedule = {
        'title': '予定',
        'start_datetime': now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1),
        'end_datetime': now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=2),
        'location': '',
        'is_all_day': False,
        'confidence': 0.3
    }
    
    # タイトルの抽出
    title_patterns = [
        r'「(.+?)」',  # 「会議」
        r'『(.+?)』',  # 『会議』
        r'(ミーティング|会議|MTG|打ち合わせ|面談|商談|説明会|セミナー|発表会|イベント)',
    ]
    
    for pattern in title_patterns:
        match = re.search(pattern, text)
        if match:
            schedule['title'] = match.group(1)
            schedule['confidence'] += 0.1
            break
    
    # 日付の抽出
    date_patterns = [
        (r'明日', now.date() + timedelta(days=1)),
        (r'明後日', now.date() + timedelta(days=2)),
        (r'(今日|本日)', now.date()),
        (r'(\d+)月(\d+)日', lambda m: datetime(now.year, int(m.group(1)), int(m.group(2))).date()),
        (r'(\d+)/(\d+)', lambda m: datetime(now.year, int(m.group(1)), int(m.group(2))).date()),
    ]
    
    for pattern, date_func in date_patterns:
        match = re.search(pattern, text)
        if match:
            if callable(date_func):
                date_obj = date_func(match)
            else:
                date_obj = date_func
            
            # 日付を設定
            schedule['confidence'] += 0.2
            break
    else:
        # デフォルトは明日
        date_obj = now.date() + timedelta(days=1)
    
    # 時間の抽出
    time_pattern = r'(\d{1,2})(?:時|:|：)(\d{0,2})'
    time_matches = list(re.finditer(time_pattern, text))
    
    if time_matches:
        schedule['confidence'] += 0.2
        
        # 開始時間
        start_hour = int(time_matches[0].group(1))
        start_minute = int(time_matches[0].group(2)) if time_matches[0].group(2) else 0
        
        # 午後の表現があれば12時間加算
        if '午後' in text[:time_matches[0].start()] or 'PM' in text[:time_matches[0].start()].upper():
            if start_hour < 12:
                start_hour += 12
        
        start_datetime = datetime.combine(date_obj, datetime.min.time().replace(hour=start_hour, minute=start_minute))
        start_datetime = tz.localize(start_datetime)
        schedule['start_datetime'] = start_datetime
        
        # 終了時間（指定があれば）
        if len(time_matches) > 1:
            end_hour = int(time_matches[1].group(1))
            end_minute = int(time_matches[1].group(2)) if time_matches[1].group(2) else 0
            
            # 午後の表現があれば12時間加算
            if '午後' in text[time_matches[0].end():time_matches[1].start()] or 'PM' in text[time_matches[0].end():time_matches[1].start()].upper():
                if end_hour < 12:
                    end_hour += 12
            
            end_datetime = datetime.combine(date_obj, datetime.min.time().replace(hour=end_hour, minute=end_minute))
            end_datetime = tz.localize(end_datetime)
            
            # 終了時間が開始時間より前なら翌日と解釈
            if end_datetime <= start_datetime:
                end_datetime = end_datetime + timedelta(days=1)
            
            schedule['end_datetime'] = end_datetime
        else:
            # 終了時間の指定がなければ開始から1時間後
            schedule['end_datetime'] = start_datetime + timedelta(hours=1)
    
    # 場所の抽出
    location_patterns = [
        r'場所は(.+?)(?:で|にて|$)',
        r'(.+?)(?:にて|で)(?:開催|行います|行う|実施)',
        r'@\s*(.+?)(?:$|\s)',
    ]
    
    for pattern in location_patterns:
        match = re.search(pattern, text)
        if match:
            schedule['location'] = match.group(1).strip()
            schedule['confidence'] += 0.1
            break
    
    return schedule