import os
import json
import pytz
from datetime import datetime, timedelta
import openai
from typing import Dict, Any, Optional, Tuple

# OpenAI API キーの設定
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
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
        # システムプロンプト
        system_prompt = (
            "あなたは予定情報を抽出する専門アシスタントです。"
            "ユーザーの文章に含まれる、これから行われる行動（予定）をできるだけ詳しく抽出してください。"
            "JSON形式で情報を返してください。"
        )
        
        # Few-Shot（例示）を追加したユーザープロンプト
        user_prompt = f"""
以下の会話から、未来の予定が含まれている場合、その内容を抽出してください。
現在の日時は {datetime.now(pytz.timezone('Asia/Tokyo')).strftime('%Y年%m月%d日 %H:%M')} です。

以下のJSONフォーマットで返してください:
```json
{{
  "title": "予定のタイトルや内容", 
  "date": "YYYY-MM-DD形式の日付",
  "start_time": "HH:MM形式の開始時間",
  "end_time": "HH:MM形式の終了時間（明示されていない場合は開始から1時間後）",
  "location": "場所（指定されていない場合は空）",
  "all_day": false/true（終日予定かどうか）,
  "confidence": 0.0～1.0の数値（この予定情報の確信度）
}}
```

もし予定が含まれていない場合や情報が不十分な場合は、confidenceを0.4未満にしてください。
特に「日付」が不明確な場合はconfidenceを下げてください。

【例1】
会話: "明日の朝9時、渋谷のスタバで打ち合わせをしよう"
```json
{{
  "title": "打ち合わせ",
  "date": "2025-03-01",
  "start_time": "09:00",
  "end_time": "10:00",
  "location": "渋谷のスタバ",
  "all_day": false,
  "confidence": 0.9
}}
```

【例2】
会話: "来週の金曜、夕方5時から映画を見に行こうよ。了解！新宿の映画館に集合ね。"
```json
{{
  "title": "映画鑑賞",
  "date": "2025-03-07",
  "start_time": "17:00",
  "end_time": "19:00",
  "location": "新宿の映画館",
  "all_day": false,
  "confidence": 0.85
}}
```

【例3】
会話: "昨日の会議はどうだった？まあまあかな。"
```json
{{
  "title": "",
  "date": "",
  "start_time": "",
  "end_time": "",
  "location": "",
  "all_day": false,
  "confidence": 0.1
}}
```

【会話】
{conversation_text}
"""

        # OpenAI APIを使用して会話を解析
        response = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0,
            response_format={"type": "json_object"}
        )
        
        # JSONレスポンスを解析
        result = json.loads(response.choices[0].message.content)
        
        # 結果の検証と調整
        confidence = float(result.get("confidence", 0))
        
        # 必須フィールドのチェック
        if not result.get("date") or not result.get("title"):
            confidence = min(confidence, 0.3)  # 日付やタイトルがなければ信頼度を下げる
        
        # 予定情報がほとんどなければ空の辞書を返す
        if confidence < 0.4:
            return {}, confidence
        
        # 予定情報を標準フォーマットに変換
        schedule_info = convert_openai_result_to_schedule(result)
        
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
        
        # 日付の解析
        date_str = result.get("date", "")
        if date_str:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
        else:
            # 日付がない場合は明日と仮定
            date_obj = (datetime.now(tz) + timedelta(days=1)).date()
        
        # 開始時間の解析
        start_time_str = result.get("start_time", "")
        if start_time_str:
            start_time = datetime.strptime(start_time_str, "%H:%M").time()
            start_datetime = datetime.combine(date_obj, start_time)
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
            end_time = datetime.strptime(end_time_str, "%H:%M").time()
            end_datetime = datetime.combine(date_obj, end_time)
            end_datetime = tz.localize(end_datetime)
            
            # 終了時間が開始時間より前の場合（例: 23:00-1:00）は翌日と解釈
            if end_datetime <= start_datetime:
                end_datetime = end_datetime + timedelta(days=1)
        else:
            # 終了時間が指定されていない場合
            if result.get("all_day"):
                # 終日予定の場合
                end_datetime = start_datetime + timedelta(days=1)
            else:
                # 通常予定の場合、開始から1時間後
                end_datetime = start_datetime + timedelta(hours=1)
        
        # 信頼度
        confidence = float(result.get("confidence", 0.5))
        
        # 標準形式のスケジュール情報を作成
        schedule_info = {
            'title': result.get("title", "予定"),
            'start_datetime': start_datetime,
            'end_datetime': end_datetime,
            'location': result.get("location", ""),
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
            'is_all_day': False,
            'confidence': 0.3
        }