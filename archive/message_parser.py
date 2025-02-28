import os
import re
from datetime import datetime, timedelta
import pytz
import json
from archive.openai_analyzer import analyze_conversation_with_openai

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
        print(f"Parsing text for scheduling: {text[:100]}...")
        
        # OpenAI APIを使用した解析を試みる
        if OPENAI_API_KEY:
            schedule_info, confidence = analyze_conversation_with_openai(text)
            
            # デバッグ情報の出力
            print(f"OpenAI analysis result - confidence: {confidence}")
            if confidence >= 0.3:
                print(f"Title: {schedule_info.get('title', 'N/A')}")
                print(f"Location: {schedule_info.get('location', 'N/A')}")
                if 'start_datetime' in schedule_info:
                    print(f"Start: {schedule_info['start_datetime'].strftime('%Y-%m-%d %H:%M')}")
                if 'end_datetime' in schedule_info:
                    print(f"End: {schedule_info['end_datetime'].strftime('%Y-%m-%d %H:%M')}")
            
            # 十分な信頼度があれば、その結果を使用する
            if confidence >= 0.4:
                # 追加のバリデーションを実施
                schedule_info = ensure_valid_schedule(schedule_info)
                return schedule_info
            # 低い信頼度の場合はフォールバックする前に内容をチェック
            elif confidence >= 0.3 and is_valid_schedule_info(schedule_info):
                print("Using lower confidence result because it appears valid")
                return schedule_info
        
        # OpenAI APIが使えない、または信頼度が低い場合は簡易パーサーで解析
        print("Falling back to simple parser")
        return simple_date_time_parser(text)
    
    except Exception as e:
        print(f"Parsing error: {e}")
        # エラー時はシンプルなパーサーにフォールバック
        try:
            return simple_date_time_parser(text)
        except:
            return None

def is_valid_schedule_info(schedule_info):
    """
    スケジュール情報が有効かどうかを確認する基本的なバリデーション
    
    Args:
        schedule_info (dict): 確認するスケジュール情報
        
    Returns:
        bool: 有効なスケジュール情報であればTrue
    """
    # 必須項目のチェック
    if not schedule_info:
        return False
    
    # タイトルの確認
    if not schedule_info.get('title'):
        return False
    
    # 開始時間の確認
    if 'start_datetime' not in schedule_info:
        return False
    
    # 終了時間の確認
    if 'end_datetime' not in schedule_info:
        return False
    
    # 時間の妥当性チェック
    if schedule_info['start_datetime'] >= schedule_info['end_datetime']:
        return False
        
    return True

def ensure_valid_schedule(schedule_info):
    """
    スケジュール情報が有効であることを確認し、必要に応じて修正する
    
    Args:
        schedule_info (dict): 確認・修正するスケジュール情報
        
    Returns:
        dict: 有効なスケジュール情報
    """
    if not schedule_info:
        schedule_info = {}
    
    tz = pytz.timezone('Asia/Tokyo')
    now = datetime.now(tz)
    
    # タイトルが空ならデフォルト値
    if not schedule_info.get('title'):
        schedule_info['title'] = "予定"
    
    # 開始時間がなければデフォルト値
    if 'start_datetime' not in schedule_info:
        # 次の時間（現在時刻の次の30分区切り）をデフォルトとする
        if now.minute < 30:
            start_datetime = now.replace(minute=30, second=0, microsecond=0)
        else:
            start_datetime = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        
        # 営業時間外なら翌日の朝10時
        if start_datetime.hour < 9 or start_datetime.hour >= 18:
            start_datetime = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        
        schedule_info['start_datetime'] = start_datetime
    
    # 終了時間がなければデフォルト値（開始時間の1時間後）
    if 'end_datetime' not in schedule_info and 'start_datetime' in schedule_info:
        schedule_info['end_datetime'] = schedule_info['start_datetime'] + timedelta(hours=1)
    
    # 過去の時間ならば将来に調整
    if 'start_datetime' in schedule_info and schedule_info['start_datetime'] < now:
        # 同じ日なら1時間後に設定
        if schedule_info['start_datetime'].date() == now.date():
            adjustment = max(1, now.hour - schedule_info['start_datetime'].hour + 1)
            schedule_info['start_datetime'] = schedule_info['start_datetime'] + timedelta(hours=adjustment)
            if 'end_datetime' in schedule_info:
                schedule_info['end_datetime'] = schedule_info['end_datetime'] + timedelta(hours=adjustment)
        else:
            # 過去の日付なら明日に設定
            days_diff = (now.date() - schedule_info['start_datetime'].date()).days + 1
            schedule_info['start_datetime'] = schedule_info['start_datetime'] + timedelta(days=days_diff)
            if 'end_datetime' in schedule_info:
                schedule_info['end_datetime'] = schedule_info['end_datetime'] + timedelta(days=days_diff)
    
    # 開始時間が終了時間より後ならば調整
    if 'start_datetime' in schedule_info and 'end_datetime' in schedule_info:
        if schedule_info['start_datetime'] >= schedule_info['end_datetime']:
            schedule_info['end_datetime'] = schedule_info['start_datetime'] + timedelta(hours=1)
    
    return schedule_info

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
        
        # 説明文とメモ
        description = openai_result.get('description', '')
        
        # 参加者情報があれば説明文に追加
        participants = openai_result.get('participants', [])
        if participants and len(participants) > 0:
            if description:
                description += "\n\n"
            description += "参加者: " + ", ".join(participants)
        
        return {
            'title': openai_result.get('title', '予定'),
            'start_datetime': start_datetime,
            'end_datetime': end_datetime,
            'location': openai_result.get('location', ''),
            'description': description,
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
            'description': '',
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
        'description': '',
        'is_all_day': False,
        'confidence': 0.3
    }
    
    # タイトルの抽出
    # より多くのキーワードとパターンを追加
    title_patterns = [
        r'「(.+?)」',  # 「会議」
        r'『(.+?)』',  # 『会議』
        r'(ミーティング|会議|MTG|打ち合わせ|面談|商談|説明会|セミナー|発表会|イベント|インタビュー|ランチ|食事|飲み会|打合せ|研修|講習|勉強会|報告会|相談|プレゼン)',
        r'について(.+?)(?:する|します|しよう)',
        r'(.+?)(?:について|を)(?:予定|設定|入れ|行[いう])',
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
        (r'明後日|あさって', now.date() + timedelta(days=2)),
        (r'(今日|本日)', now.date()),
        (r'(\d+)月(\d+)日', lambda m: datetime(now.year, int(m.group(1)), int(m.group(2))).date()),
        (r'(\d+)/(\d+)', lambda m: datetime(now.year, int(m.group(1)), int(m.group(2))).date()),
        (r'来週(\d+)月(\d+)日', lambda m: datetime(now.year, int(m.group(1)), int(m.group(2))).date() + timedelta(days=7)),
        (r'来週の?([月火水木金土日])', lambda m: next_weekday(now, m.group(1))),
        (r'(\d+)日後', lambda m: now.date() + timedelta(days=int(m.group(1)))),
    ]
    
    date_obj = now.date() + timedelta(days=1)  # デフォルトは明日
    
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
    
    # 時間の抽出（より多くのパターンを認識）
    time_patterns = [
        r'(\d{1,2})(?:時|:|：)(\d{0,2})',  # 15時、15:00
        r'午前(\d{1,2})(?:時|:|：)(\d{0,2})?',  # 午前10時
        r'午後(\d{1,2})(?:時|:|：)(\d{0,2})?',  # 午後3時
        r'正午',  # 正午
        r'(朝|昼|夕方|夜)',  # 朝、昼、夕方、夜
    ]
    
    time_matches = []
    for pattern in time_patterns:
        matches = list(re.finditer(pattern, text))
        time_matches.extend(matches)
    
    # マッチした位置でソート
    time_matches.sort(key=lambda x: x.start())
    
    if time_matches:
        schedule['confidence'] += 0.2
        
        # 開始時間
        first_match = time_matches[0]
        
        if '正午' in first_match.group(0):
            start_hour, start_minute = 12, 0
        elif '朝' in first_match.group(0):
            start_hour, start_minute = 9, 0
        elif '昼' in first_match.group(0):
            start_hour, start_minute = 12, 0
        elif '夕方' in first_match.group(0):
            start_hour, start_minute = 17, 0
        elif '夜' in first_match.group(0):
            start_hour, start_minute = 19, 0
        elif '午前' in first_match.group(0):
            start_hour = int(first_match.group(1))
            start_minute = int(first_match.group(2)) if first_match.group(2) else 0
        elif '午後' in first_match.group(0):
            start_hour = int(first_match.group(1)) + 12 if int(first_match.group(1)) < 12 else int(first_match.group(1))
            start_minute = int(first_match.group(2)) if first_match.group(2) else 0
        else:
            # 通常の時間表記
            start_hour = int(first_match.group(1))
            start_minute = int(first_match.group(2)) if first_match.group(2) and first_match.group(2).strip() else 0
            
            # 文脈から午前/午後を判断
            if '午後' in text[:first_match.start()] or 'PM' in text[:first_match.start()].upper():
                if start_hour < 12:
                    start_hour += 12
        
        start_datetime = datetime.combine(date_obj, datetime.min.time().replace(hour=start_hour, minute=start_minute))
        start_datetime = tz.localize(start_datetime)
        schedule['start_datetime'] = start_datetime
        
        # 終了時間（指定があれば）
        if len(time_matches) > 1:
            second_match = time_matches[1]
            
            if '正午' in second_match.group(0):
                end_hour, end_minute = 12, 0
            elif '朝' in second_match.group(0):
                end_hour, end_minute = 9, 0
            elif '昼' in second_match.group(0):
                end_hour, end_minute = 12, 0
            elif '夕方' in second_match.group(0):
                end_hour, end_minute = 17, 0
            elif '夜' in second_match.group(0):
                end_hour, end_minute = 19, 0
            elif '午前' in second_match.group(0):
                end_hour = int(second_match.group(1))
                end_minute = int(second_match.group(2)) if second_match.group(2) else 0
            elif '午後' in second_match.group(0):
                end_hour = int(second_match.group(1)) + 12 if int(second_match.group(1)) < 12 else int(second_match.group(1))
                end_minute = int(second_match.group(2)) if second_match.group(2) else 0
            else:
                # 通常の時間表記
                end_hour = int(second_match.group(1))
                end_minute = int(second_match.group(2)) if second_match.group(2) and second_match.group(2).strip() else 0
                
                # 文脈から午前/午後を判断
                if '午後' in text[first_match.end():second_match.start()] or 'PM' in text[first_match.end():second_match.start()].upper():
                    if end_hour < 12:
                        end_hour += 12
            
            end_datetime = datetime.combine(date_obj, datetime.min.time().replace(hour=end_hour, minute=end_minute))
            end_datetime = tz.localize(end_datetime)
            
            # 終了時間が開始時間より前なら翌日と解釈
            if end_datetime <= start_datetime:
                end_datetime = end_datetime + timedelta(days=1)
            
            schedule['end_datetime'] = end_datetime
        else:
            # 終了時間の指定がない場合、開始から1時間後が基本
            # イベントタイプに基づいて調整
            title = schedule['title'].lower()
            if any(keyword in title for keyword in ['会議', 'ミーティング', 'mtg']):
                schedule['end_datetime'] = start_datetime + timedelta(hours=1)
            elif any(keyword in title for keyword in ['ランチ', '食事', '飲み会']):
                schedule['end_datetime'] = start_datetime + timedelta(hours=1, minutes=30)
            elif any(keyword in title for keyword in ['セミナー', '研修', '講習']):
                schedule['end_datetime'] = start_datetime + timedelta(hours=2)
            else:
                # 所要時間の明示的な指定を探す
                duration_match = re.search(r'(\d+)時間', text)
                if duration_match:
                    hours = int(duration_match.group(1))
                    schedule['end_datetime'] = start_datetime + timedelta(hours=hours)
                else:
                    duration_match = re.search(r'(\d+)分', text)
                    if duration_match:
                        minutes = int(duration_match.group(1))
                        schedule['end_datetime'] = start_datetime + timedelta(minutes=minutes)
                    else:
                        # デフォルトは1時間
                        schedule['end_datetime'] = start_datetime + timedelta(hours=1)
    
    # 場所の抽出（より多くのパターンを認識）
    location_patterns = [
        r'場所[はが](.+?)(?:で|にて|に|$)',
        r'(.+?)(?:にて|で)(?:開催|行います|行う|実施)',
        r'@\s*(.+?)(?:$|\s)',
        r'(.+?)(?:に集合|で待ち合わせ)',
        r'「(.+?)」(?:で|にて)',
    ]
    
    for pattern in location_patterns:
        match = re.search(pattern, text)
        if match:
            location = match.group(1).strip()
            # 余分な記号や文字を削除
            location = re.sub(r'[、。.」「]', '', location)
            schedule['location'] = location
            schedule['confidence'] += 0.1
            break
    
    # 場所の具体的なキーワード検索
    location_keywords = [
        '会議室', 'オフィス', '本社', '支社', 'カフェ', 'レストラン',
        'ホテル', 'オンライン', 'Zoom', 'Teams', 'Google Meet', 'Webex'
    ]
    
    if not schedule['location']:
        for keyword in location_keywords:
            if keyword in text:
                schedule['location'] = keyword
                schedule['confidence'] += 0.1
                break
    
    # 終日イベント判定
    if '終日' in text or '一日中' in text or '丸一日' in text:
        schedule['is_all_day'] = True
        start_datetime = datetime.combine(date_obj, datetime.min.time())
        start_datetime = tz.localize(start_datetime)
        schedule['start_datetime'] = start_datetime
        schedule['end_datetime'] = start_datetime + timedelta(days=1)
        schedule['confidence'] += 0.1
    
    # 説明文の抽出
    # タイトルと場所が特定できた場合は、それらを除く部分を説明として使用
    if schedule['title'] != '予定' and schedule['location']:
        # タイトルと場所を除いたテキストを説明として使用
        description = text
        # タイトルを除去
        description = description.replace(schedule['title'], '')
        # 場所を除去
        description = description.replace(schedule['location'], '')
        # 余分な空白や記号を削除
        description = re.sub(r'\s+', ' ', description).strip()
        if len(description) > 20:  # 長すぎる説明は切り詰める
            description = description[:150] + '...' if len(description) > 150 else description
        schedule['description'] = description
    
    return schedule

def next_weekday(now, weekday_str):
    """指定された曜日の次の日付を返す"""
    weekday_map = {'月': 0, '火': 1, '水': 2, '木': 3, '金': 4, '土': 5, '日': 6}
    target_weekday = weekday_map.get(weekday_str, 0)
    
    days_ahead = target_weekday - now.weekday()
    if days_ahead <= 0:  # ターゲットの曜日が今日または過去の曜日の場合
        days_ahead += 7
    
    return now.date() + timedelta(days=days_ahead)