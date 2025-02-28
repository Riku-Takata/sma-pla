import os
import json
import pytz
from datetime import datetime, timedelta
import openai
from typing import Dict, Any, Optional, Tuple

# OpenAI API キーの設定
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
if OPENAI_API_KEY:
    openai.api_key = OPENAI_API_KEY

def analyze_conversation_with_openai(conversation_text: str) -> Tuple[Dict[str, Any], float]:
    """
    OpenAIを使用して会話テキストから予定情報を抽出する強化版
    
    Args:
        conversation_text (str): 解析する会話テキスト
        
    Returns:
        Tuple[Dict[str, Any], float]: 予定情報と信頼度のタプル
    """
    if not OPENAI_API_KEY:
        print("Warning: OPENAI_API_KEY not set. Skipping OpenAI analysis.")
        return {}, 0.0
    
    try:
        # 現在の日時情報（日本時間）
        jst = pytz.timezone('Asia/Tokyo')
        now = datetime.now(jst)
        current_date_str = now.strftime("%Y年%m月%d日")
        current_time_str = now.strftime("%H:%M")
        current_weekday = ["月", "火", "水", "木", "金", "土", "日"][now.weekday()]
        
        # システムプロンプト - より明確で詳細な指示を提供
        system_prompt = (
            "あなたは日本の会話から予定情報を正確に抽出する専門AIです。"
            "ユーザーの会話から、これから行われる予定に関する情報を詳細に分析して抽出してください。"
            "日本語の曖昧な表現や、文脈から推測できる情報も考慮して、できるだけ正確な予定詳細を特定してください。"
            "既に過去の出来事については予定として抽出せず、未来の予定のみを抽出してください。"
        )
        
        # ユーザープロンプト - より多くの例と明確な指示
        user_prompt = f"""
以下の会話から、未来の予定情報を抽出してください。

【現在の日時情報】
- 今日: {current_date_str}({current_weekday}) {current_time_str}
- 曜日: 今日は{current_weekday}曜日

以下のJSONフォーマットで返してください:
```json
{{
  "title": "予定のタイトルや内容（会議名、イベント名など）", 
  "date": "YYYY-MM-DD形式の日付",
  "start_time": "HH:MM形式の開始時間",
  "end_time": "HH:MM形式の終了時間（明示されていない場合は開始から1時間後）",
  "location": "場所（指定されていない場合は空欄、オンラインの場合は「オンライン」）",
  "description": "予定の詳細説明",
  "all_day": false/true（終日予定かどうか）,
  "participants": ["参加者1", "参加者2"],
  "confidence": 0.0～1.0の数値（この予定情報の確信度）
}}
```

【重要なポイント】
1. 時間表現は24時間制で返してください（例: 午後3時→15:00）
2. 「明日」「来週」などの相対的な表現は具体的な日付に変換してください
3. 「朝」「昼」「夕方」などの曖昧な時間表現は適切な時間に変換してください:
   - 朝: 8:00〜10:00頃
   - 昼/お昼: 12:00頃
   - 午後: 13:00〜16:00頃
   - 夕方: 17:00〜18:00頃
   - 夜: 19:00〜21:00頃
4. 「〜時から2時間」などの期間表現から終了時間を計算してください
5. 予定が含まれていない場合や情報が極端に不足している場合は、confidenceを0.3未満にしてください
6. 過去の予定ではなく、これから行われる未来の予定のみを抽出してください

【例1】
会話: "明日の朝9時に渋谷のスタバで打ち合わせしましょう。新しいプロジェクトについて話し合いたいです。" "了解です。明日9時に渋谷スタバで会いましょう。"
```json
{{
  "title": "新しいプロジェクトの打ち合わせ",
  "date": "2025-03-01",
  "start_time": "09:00",
  "end_time": "10:30",
  "location": "渋谷のスターバックス",
  "description": "新しいプロジェクトについての話し合い",
  "all_day": false,
  "participants": [],
  "confidence": 0.95
}}
```

【例2】
会話: "来週の水曜、社内MTGやりません？" "いいですね。何時がいいですか？" "15時からでどうですか？会議室Aを予約しておきます" "了解です"
```json
{{
  "title": "社内MTG",
  "date": "2025-03-05",
  "start_time": "15:00",
  "end_time": "16:00",
  "location": "会議室A",
  "description": "社内ミーティング",
  "all_day": false,
  "participants": [],
  "confidence": 0.9
}}
```

【例3】
会話: "昨日の会議はどうだった？" "まあまあかな。特に新しい決定事項はなかったよ。"
```json
{{
  "title": "",
  "date": "",
  "start_time": "",
  "end_time": "",
  "location": "",
  "description": "",
  "all_day": false,
  "participants": [],
  "confidence": 0.1
}}
```

【例4】
会話: "4/15に東京オフィスで終日の戦略会議があります。全部門長は参加必須です。" "了解しました。カレンダーに入れておきます。"
```json
{{
  "title": "戦略会議",
  "date": "2025-04-15",
  "start_time": "09:00",
  "end_time": "18:00",
  "location": "東京オフィス",
  "description": "全部門長参加の終日戦略会議",
  "all_day": true,
  "participants": ["全部門長"],
  "confidence": 0.95
}}
```

【例5】
会話: "明後日18時から2時間、オンラインでクライアントミーティングを行います。プレゼン資料を準備しておいてください。" "承知しました。Zoomリンクは後ほど送ります。"
```json
{{
  "title": "クライアントミーティング",
  "date": "2025-03-02",
  "start_time": "18:00",
  "end_time": "20:00",
  "location": "オンライン",
  "description": "クライアント向けプレゼンテーション、Zoomで実施",
  "all_day": false,
  "participants": [],
  "confidence": 0.9
}}
```

【分析対象の会話】
{conversation_text}
"""

        # OpenAI APIを使用して会話を解析（GPT-4を使用するとさらに精度が向上）
        response = openai.chat.completions.create(
            model="gpt-4",  # または "gpt-4" 
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,  # より決定論的な応答のために温度を下げる
            response_format={"type": "json_object"}
        )
        
        # JSONレスポンスを解析
        result = json.loads(response.choices[0].message.content)
        
        # 結果の検証と調整
        confidence = float(result.get("confidence", 0))
        
        # 必須フィールドのチェックと信頼度調整
        missing_fields = []
        required_fields = ["title", "date", "start_time"]
        
        for field in required_fields:
            if not result.get(field):
                missing_fields.append(field)
        
        # 必須フィールドが欠けている場合は信頼度を大幅に下げる
        if missing_fields:
            confidence_penalty = 0.3 * len(missing_fields)
            confidence = max(0.1, confidence - confidence_penalty)
        
        # 予定情報がほとんどなければ空の辞書を返す
        if confidence < 0.4:
            return {}, confidence
        
        # 予定情報を標準フォーマットに変換
        schedule_info = convert_openai_result_to_schedule(result)
        
        # バリデーション
        schedule_info = validate_schedule_info(schedule_info)
        
        return schedule_info, confidence
        
    except Exception as e:
        print(f"Error analyzing conversation with OpenAI: {e}")
        return {}, 0.0

def convert_openai_result_to_schedule(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    OpenAIの結果を標準的なスケジュール形式に変換
    
    Args:
        result (Dict[str, Any]): OpenAIからの解析結果
        
    Returns:
        Dict[str, Any]: スケジュール情報
    """
    try:
        # タイムゾーン設定
        tz = pytz.timezone('Asia/Tokyo')
        
        # 現在の日時
        now = datetime.now(tz)
        
        # 日付の解析
        date_str = result.get("date", "")
        if date_str:
            try:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                # 日付形式が異なる場合のフォールバック
                print(f"Warning: Invalid date format: {date_str}. Using tomorrow as fallback.")
                date_obj = (now + timedelta(days=1)).date()
        else:
            # 日付がない場合は明日と仮定
            date_obj = (now + timedelta(days=1)).date()
        
        # 開始時間の解析
        start_time_str = result.get("start_time", "")
        if start_time_str:
            try:
                start_time = datetime.strptime(start_time_str, "%H:%M").time()
                start_datetime = datetime.combine(date_obj, start_time)
                start_datetime = tz.localize(start_datetime)
            except ValueError:
                # 時間形式が異なる場合のフォールバック
                print(f"Warning: Invalid start_time format: {start_time_str}. Using 10:00 as fallback.")
                start_datetime = datetime.combine(date_obj, datetime.min.time().replace(hour=10))
                start_datetime = tz.localize(start_datetime)
        else:
            # 開始時間が指定されていない場合
            if result.get("all_day"):
                # 終日予定の場合
                start_datetime = datetime.combine(date_obj, datetime.min.time())
                start_datetime = tz.localize(start_datetime)
            else:
                # 通常予定で時間未指定の場合、午前10時と仮定
                start_datetime = datetime.combine(date_obj, datetime.min.time().replace(hour=10))
                start_datetime = tz.localize(start_datetime)
        
        # 終了時間の解析
        end_time_str = result.get("end_time", "")
        if end_time_str:
            try:
                end_time = datetime.strptime(end_time_str, "%H:%M").time()
                end_datetime = datetime.combine(date_obj, end_time)
                end_datetime = tz.localize(end_datetime)
                
                # 終了時間が開始時間より前の場合（例: 23:00-1:00）は翌日と解釈
                if end_datetime <= start_datetime:
                    end_datetime = end_datetime + timedelta(days=1)
            except ValueError:
                # 時間形式が異なる場合のフォールバック
                print(f"Warning: Invalid end_time format: {end_time_str}. Using start_time + 1 hour as fallback.")
                end_datetime = start_datetime + timedelta(hours=1)
        else:
            # 終了時間が指定されていない場合
            if result.get("all_day"):
                # 終日予定の場合
                end_datetime = start_datetime + timedelta(days=1)
            else:
                # 通常予定の場合、開始から適切な時間後
                # 会議やMTGなどのキーワードによって適切な所要時間を設定
                title = result.get("title", "").lower()
                description = result.get("description", "").lower()
                
                if any(keyword in title.lower() or keyword in description.lower() 
                      for keyword in ["会議", "ミーティング", "mtg", "meeting", "打ち合わせ"]):
                    # 会議系は1時間
                    end_datetime = start_datetime + timedelta(hours=1)
                elif any(keyword in title.lower() or keyword in description.lower() 
                      for keyword in ["ランチ", "昼食", "lunch", "食事", "飲み会", "dinner"]):
                    # 食事系は1.5時間
                    end_datetime = start_datetime + timedelta(minutes=90)
                elif any(keyword in title.lower() or keyword in description.lower() 
                      for keyword in ["セミナー", "研修", "workshop", "ワークショップ", "講習"]):
                    # セミナー系は3時間
                    end_datetime = start_datetime + timedelta(hours=3)
                else:
                    # デフォルトは1時間
                    end_datetime = start_datetime + timedelta(hours=1)
        
        # 信頼度
        confidence = float(result.get("confidence", 0.5))
        
        # 説明文のフォーマット
        description = result.get("description", "")
        participants = result.get("participants", [])
        if participants and isinstance(participants, list) and len(participants) > 0:
            if description:
                description += "\n\n参加者: " + ", ".join(participants)
            else:
                description = "参加者: " + ", ".join(participants)
        
        # 標準形式のスケジュール情報を作成
        schedule_info = {
            'title': result.get("title", "予定"),
            'start_datetime': start_datetime,
            'end_datetime': end_datetime,
            'location': result.get("location", ""),
            'description': description,
            'is_all_day': bool(result.get("all_day", False)),
            'confidence': confidence
        }
        
        return schedule_info
    
    except Exception as e:
        print(f"Error converting OpenAI result to schedule: {e}")
        # エラー時はデフォルト値
        now = datetime.now(pytz.timezone('Asia/Tokyo'))
        tomorrow = now + timedelta(days=1)
        return {
            'title': result.get("title", "予定"),
            'start_datetime': tomorrow.replace(hour=10, minute=0, second=0, microsecond=0),
            'end_datetime': tomorrow.replace(hour=11, minute=0, second=0, microsecond=0),
            'location': result.get("location", ""),
            'description': "",
            'is_all_day': False,
            'confidence': 0.3
        }

def validate_schedule_info(schedule_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    スケジュール情報の妥当性を検証し、必要に応じて調整する
    
    Args:
        schedule_info (Dict[str, Any]): 検証するスケジュール情報
        
    Returns:
        Dict[str, Any]: 検証・調整されたスケジュール情報
    """
    # タイムゾーンの確認と設定
    tz = pytz.timezone('Asia/Tokyo')
    now = datetime.now(tz)
    
    # タイトルのデフォルト値設定
    if not schedule_info.get('title'):
        schedule_info['title'] = "予定"
    
    # 開始時間が過去でないことを確認
    if schedule_info.get('start_datetime') and schedule_info['start_datetime'] < now:
        # 過去の時間の場合、翌日の同時刻に設定
        days_to_add = 1
        if schedule_info['start_datetime'].date() == now.date():
            # 同じ日なら翌日に
            pass
        else:
            # 過去の日付の場合、来年の同じ日に
            current_year = now.year
            next_year = current_year + 1
            start_dt = schedule_info['start_datetime']
            start_dt = start_dt.replace(year=next_year)
            days_to_add = (start_dt.date() - now.date()).days
            if days_to_add < 0:
                days_to_add = 1  # 最低でも明日に設定
        
        # 日付を調整
        schedule_info['start_datetime'] += timedelta(days=days_to_add)
        if schedule_info.get('end_datetime'):
            schedule_info['end_datetime'] += timedelta(days=days_to_add)
    
    # 開始時間より終了時間が後であることを確認
    if (schedule_info.get('start_datetime') and schedule_info.get('end_datetime') and 
            schedule_info['start_datetime'] >= schedule_info['end_datetime']):
        # 終了時間が開始時間より前の場合、開始から1時間後に設定
        schedule_info['end_datetime'] = schedule_info['start_datetime'] + timedelta(hours=1)
    
    # 終日イベントの場合の時間設定
    if schedule_info.get('is_all_day') and schedule_info.get('start_datetime'):
        start_date = schedule_info['start_datetime'].date()
        schedule_info['start_datetime'] = tz.localize(datetime.combine(start_date, datetime.min.time()))
        schedule_info['end_datetime'] = schedule_info['start_datetime'] + timedelta(days=1)
    
    return schedule_info