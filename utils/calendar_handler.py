def find_next_available_time(user_id, start_time, duration_minutes, max_days=7):
    """
    次に空いている時間を探す関数
    
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