import re
from datetime import datetime, timedelta
import pytz
import os
import json

# 環境変数からAPIキーを取得
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# 日本語の曜日と数字のマッピング
WEEKDAY_MAP = {'月': 0, '火': 1, '水': 2, '木': 3, '金': 4, '土': 5, '日': 6}

# 時間帯の表現マッピング
TIME_EXPRESSION_MAP = {
    '朝': '8:00',
    '午前': '10:00',
    '昼': '12:00',
    '午後': '14:00',
    '夕方': '17:00',
    '夜': '19:00',
    '夜遅く': '22:00',
}

def parse_schedule_from_text(text, timezone='Asia/Tokyo'):
    """
    テキストから予定情報を抽出する
    
    Args:
        text (str): 予定が含まれるテキスト
        timezone (str): タイムゾーン (デフォルト: 'Asia/Tokyo')
    
    Returns:
        dict: 予定情報を含む辞書
    """
    # OpenAI APIが利用可能な場合はそちらを優先
    if OPENAI_API_KEY:
        try:
            return extract_event_info_with_openai(text, timezone)
        except Exception as e:
            print(f"OpenAI API error: {e}. Falling back to rule-based parser.")
    
    # フォールバック：ルールベースのパーサー
    return rule_based_parser(text, timezone)

def extract_event_info_with_openai(text, timezone='Asia/Tokyo'):
    """
    OpenAI APIを使用して文章から予定情報を抽出する
    
    Args:
        text (str): 予定が含まれるテキスト
        timezone (str): タイムゾーン
        
    Returns:
        dict: 予定情報を含む辞書
    """
    try:
        import openai
        openai.api_key = OPENAI_API_KEY
        
        # 現在の日時
        now = datetime.now(pytz.timezone(timezone))
        
        response = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": f"""
                あなたは予定情報を抽出する専門家です。
                文章から以下の情報を抽出し、JSON形式で返してください:
                - summary: イベントの名前またはタイトル。文章から適切なタイトルを推測してください。必ず設定してください。
                - date: YYYY-MM-DD形式の日付
                - time: HH:MM形式の開始時間
                - duration: 分単位のイベント時間
                - location: イベントの場所
                - description: イベントの説明や詳細
                - all_day: 終日イベントかどうかのフラグ (true/false)

                日付や時間が明示的に示されていない場合は、文脈から推測してください。
                今日・明日・明後日などの相対的な日付は、現在の日付（{now.strftime("%Y-%m-%d")}）から計算してください。
                もし予定の種類が会議、面談、打ち合わせなどであれば、デフォルトの所要時間は60分と設定してください。
                明確な場所が指定されていない場合は、「オフィス」や「会議室」などの妥当な場所を設定してください。
                終日イベントに関する言及があれば、all_dayをtrueに設定してください。
                JSONのみを返してください。
                """},
                {"role": "user", "content": text}
            ],
            response_format={"type": "json_object"}
        )

        # レスポンスからJSONを抽出
        result = json.loads(response.choices[0].message.content)
        
        # OpenAIの結果を内部フォーマットに変換
        tz = pytz.timezone(timezone)
        start_date = datetime.strptime(result['date'], "%Y-%m-%d").date()
        
        if result.get('all_day', False):
            # 終日イベント
            start_datetime = datetime.combine(start_date, datetime.min.time())
            start_datetime = tz.localize(start_datetime)
            end_datetime = start_datetime + timedelta(days=1)
        else:
            # 通常イベント
            start_time = datetime.strptime(result['time'], "%H:%M").time()
            start_datetime = datetime.combine(start_date, start_time)
            start_datetime = tz.localize(start_datetime)
            
            duration = int(result.get('duration', 60))  # デフォルト1時間
            end_datetime = start_datetime + timedelta(minutes=duration)
        
        return {
            'title': result['summary'],
            'start_datetime': start_datetime,
            'end_datetime': end_datetime,
            'location': result.get('location', ''),
            'description': result.get('description', ''),
            'confidence': 0.8,  # OpenAI APIの結果は信頼度が高い
        }
    
    except Exception as e:
        print(f"Error using OpenAI API: {e}")
        raise

def rule_based_parser(text, timezone='Asia/Tokyo'):
    """
    ルールベースで予定情報を抽出する
    
    Args:
        text (str): 予定が含まれるテキスト
        timezone (str): タイムゾーン
        
    Returns:
        dict: 予定情報を含む辞書
    """
    # 現在日時を取得
    now = datetime.now(pytz.timezone(timezone))
    
    # デフォルト設定
    schedule_info = {
        'title': None,
        'start_datetime': None,
        'end_datetime': None,
        'location': None,
        'description': text,
        'confidence': 0.0,
    }
    
    # タイトル抽出の試み
    title_patterns = [
        r'「(.+?)」',  # 「会議」のようなパターン
        r'『(.+?)』',  # 『会議』のようなパターン
        r'について(.+?)する',  # 〜について会議する
        r'(.+?)(?:について|を)(?:予定|設定|入れ)',  # 会議について予定を入れる
        r'(?:ミーティング|会議|打ち合わせ|MTG)(?:を|は)?(.+?)(します|する|やる|行う)',  # ミーティングをXXXします
        r'(.+?)(?:の|に関する)(?:ミーティング|会議|打ち合わせ|MTG)',  # XXXに関する会議
    ]
    
    for pattern in title_patterns:
        match = re.search(pattern, text)
        if match:
            schedule_info['title'] = match.group(1).strip()
            schedule_info['confidence'] += 0.2
            break
    
    # タイトルが見つからない場合はキーワードベースで推測
    if not schedule_info['title']:
        keywords = ['ミーティング', '会議', '打ち合わせ', 'MTG', '面談', '商談', 'イベント', '発表']
        for keyword in keywords:
            if keyword in text:
                schedule_info['title'] = keyword
                schedule_info['confidence'] += 0.1
                break
    
    # それでもタイトルが見つからない場合は一般的な名詞を抽出
    if not schedule_info['title']:
        # 助詞を除去して名詞を抽出する簡易ロジック
        words = text.split()
        potential_titles = [w for w in words if len(w) >= 2 and not any(p in w for p in ['は', 'が', 'を', 'に', 'で', 'と', 'から', 'まで', 'より'])]
        if potential_titles:
            schedule_info['title'] = potential_titles[0]
            schedule_info['confidence'] += 0.1
        else:
            # デフォルトタイトル
            schedule_info['title'] = "予定"
    
    # 日付の抽出
    date_patterns = [
        # 明日、明後日などの表現
        (r'明日', now.date() + timedelta(days=1)),
        (r'明後日', now.date() + timedelta(days=2)),
        (r'(今日|本日)', now.date()),
        
        # 曜日表現 (例: 今週の火曜日、来週の水曜)
        (r'今週の?([月火水木金土日])曜', lambda match: next_weekday(now, WEEKDAY_MAP[match.group(1)])),
        (r'来週の?([月火水木金土日])曜', lambda match: next_weekday(now, WEEKDAY_MAP[match.group(1)], 1)),
        
        # 日付表現 (例: 3月15日)
        (r'(\d+)月(\d+)日', lambda match: date_from_month_day(now, int(match.group(1)), int(match.group(2)))),
        
        # MM/DD形式
        (r'(\d{1,2})/(\d{1,2})', lambda match: date_from_month_day(now, int(match.group(1)), int(match.group(2)))),
    ]
    
    target_date = None
    for pattern, date_func in date_patterns:
        match = re.search(pattern, text)
        if match:
            if callable(date_func):
                target_date = date_func(match)
            else:
                target_date = date_func
            schedule_info['confidence'] += 0.3
            break
    
    # 時間の抽出
    time_patterns = [
        # HH:MM形式
        r'(\d{1,2}):(\d{2})',
        # HH時MM分形式
        r'(\d{1,2})時(?:(\d{1,2})分)?',
        # 時間帯表現
        r'(朝|昼|午後|夕方|夜|夜遅く)',
    ]
    
    start_time = None
    end_time = None
    
    for pattern in time_patterns:
        matches = re.finditer(pattern, text)
        times = []
        
        for match in matches:
            if '朝' in match.group() or '昼' in match.group() or '午後' in match.group() or '夕方' in match.group() or '夜' in match.group():
                # 時間帯表現の場合
                time_str = TIME_EXPRESSION_MAP.get(match.group(1), '9:00')
                hour, minute = map(int, time_str.split(':'))
                times.append((hour, minute))
            elif '時' in match.group():
                # HH時MM分形式
                hour = int(match.group(1))
                minute = int(match.group(2)) if match.group(2) else 0
                
                # 午前/午後の処理
                if '午後' in text[:match.start()] or 'PM' in text[:match.start()].upper():
                    if hour < 12:
                        hour += 12
                
                times.append((hour, minute))
            else:
                # HH:MM形式
                hour = int(match.group(1))
                minute = int(match.group(2))
                
                # 午前/午後の処理
                if '午後' in text[:match.start()] or 'PM' in text[:match.start()].upper():
                    if hour < 12:
                        hour += 12
                
                times.append((hour, minute))
        
        if times:
            times.sort()  # 時間順にソート
            start_time = times[0]
            if len(times) > 1:
                end_time = times[1]
            schedule_info['confidence'] += 0.3
            break
    
    # 所要時間の抽出
    duration_match = re.search(r'(\d+)\s*時間', text)
    duration_minutes = 60  # デフォルト1時間
    if duration_match:
        duration_minutes = int(duration_match.group(1)) * 60
        schedule_info['confidence'] += 0.1
    else:
        duration_match = re.search(r'(\d+)\s*分', text)
        if duration_match:
            duration_minutes = int(duration_match.group(1))
            schedule_info['confidence'] += 0.1
    
    # 日付と時間を結合して開始・終了日時を設定
    if target_date:
        if start_time:
            start_datetime = datetime.combine(target_date, datetime.min.time().replace(hour=start_time[0], minute=start_time[1]))
            schedule_info['start_datetime'] = pytz.timezone(timezone).localize(start_datetime)
            
            # 終了時間が指定されていなければ、開始から所要時間後をデフォルトに
            if end_time:
                end_datetime = datetime.combine(target_date, datetime.min.time().replace(hour=end_time[0], minute=end_time[1]))
                schedule_info['end_datetime'] = pytz.timezone(timezone).localize(end_datetime)
            else:
                schedule_info['end_datetime'] = schedule_info['start_datetime'] + timedelta(minutes=duration_minutes)
        else:
            # 時間が指定されていない場合、9時開始をデフォルトとする
            start_datetime = datetime.combine(target_date, datetime.min.time().replace(hour=9, minute=0))
            schedule_info['start_datetime'] = pytz.timezone(timezone).localize(start_datetime)
            schedule_info['end_datetime'] = schedule_info['start_datetime'] + timedelta(minutes=duration_minutes)
    else:
        # 日付が指定されていない場合は明日を仮定
        tomorrow = now.date() + timedelta(days=1)
        if start_time:
            start_datetime = datetime.combine(tomorrow, datetime.min.time().replace(hour=start_time[0], minute=start_time[1]))
            schedule_info['start_datetime'] = pytz.timezone(timezone).localize(start_datetime)
            
            if end_time:
                end_datetime = datetime.combine(tomorrow, datetime.min.time().replace(hour=end_time[0], minute=end_time[1]))
                schedule_info['end_datetime'] = pytz.timezone(timezone).localize(end_datetime)
            else:
                schedule_info['end_datetime'] = schedule_info['start_datetime'] + timedelta(minutes=duration_minutes)
    
    # 場所の抽出
    location_patterns = [
        r'場所は(.+?)(?:で|にて|$)',
        r'(.+?)(?:にて|で)(?:開催|行います|行う|実施)',
        r'@\s*(.+?)(?:$|\s)',
        r'in\s+(.+?)(?:$|\s)',
    ]
    
    for pattern in location_patterns:
        match = re.search(pattern, text)
        if match:
            schedule_info['location'] = match.group(1).strip()
            schedule_info['confidence'] += 0.2
            break
    
    # キーワードで場所を推測
    location_keywords = {
        '会議室': '会議室',
        'オフィス': 'オフィス',
        '本社': '本社',
        '支社': '支社',
        'カフェ': 'カフェ',
        'レストラン': 'レストラン',
        'ホテル': 'ホテル',
    }
    
    if not schedule_info['location']:
        for keyword, location in location_keywords.items():
            if keyword in text:
                schedule_info['location'] = location
                schedule_info['confidence'] += 0.1
                break
    
    return schedule_info

def next_weekday(now, weekday, weeks_offset=0):
    """指定された曜日の次の日付を返す"""
    days_ahead = weekday - now.weekday()
    if days_ahead <= 0:  # ターゲットの曜日が今日または過去の曜日の場合
        days_ahead += 7
    
    days_ahead += 7 * weeks_offset  # 来週以降の場合
    return now.date() + timedelta(days=days_ahead)

def date_from_month_day(now, month, day):
    """月と日から日付を生成する、年は自動調整"""
    year = now.year
    
    # 指定された月が現在の月より前で、日付も今日より前なら来年と判断
    if month < now.month or (month == now.month and day < now.day):
        year += 1
        
    return datetime(year, month, day).date()

# 以下はテスト用
if __name__ == "__main__":
    test_texts = [
        "明日の15時から会議室でプロジェクトミーティングを行います",
        "来週の水曜日10時からオフィスで営業会議",
        "3月15日に終日研修があります",
        "明後日の午後、クライアントとの打ち合わせ",
    ]
    
    for text in test_texts:
        result = parse_schedule_from_text(text)
        print(f"Text: {text}")
        print(f"Result: {result}")
        print("---")