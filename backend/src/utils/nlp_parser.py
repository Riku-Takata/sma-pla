"""
NLP Parser - 会話テキストから予定情報を抽出するモジュール
OpenAI APIと組み合わせた解析機能を提供します
"""
import os
import re
import json
import logging
from datetime import datetime, time, timedelta
import pytz
from typing import Dict, Any, Optional, Tuple, List, Union

# OpenAI APIクライアントをインポート（存在する場合）
try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

# ロギング設定
logger = logging.getLogger(__name__)

# OpenAI API キーの設定
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
if OPENAI_API_KEY and OPENAI_AVAILABLE:
    openai.api_key = OPENAI_API_KEY

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

def parse_schedule_from_text(text: str, timezone: str = 'Asia/Tokyo') -> Dict[str, Any]:
    """
    テキストから予定情報を抽出する

    Args:
        text (str): 予定が含まれるテキスト
        timezone (str): タイムゾーン (デフォルト: 'Asia/Tokyo')

    Returns:
        dict: 予定情報を含む辞書
    """
    # OpenAI APIが利用可能かつキーが設定されている場合はそちらを優先
    if OPENAI_AVAILABLE and OPENAI_API_KEY:
        try:
            logger.info("OpenAI APIを使用してテキストを解析します")
            schedule_info, confidence = analyze_conversation_with_openai(text)
            
            # 十分な信頼度があれば、その結果を使用する
            if confidence >= 0.4:
                logger.info(f"OpenAI解析結果: {schedule_info}, 信頼度: {confidence}")
                # 追加のバリデーションを実施
                schedule_info = ensure_valid_schedule(schedule_info)
                return schedule_info
            # 低い信頼度の場合はフォールバックする前に内容をチェック
            elif confidence >= 0.3 and is_valid_schedule_info(schedule_info):
                logger.info(f"低信頼度の結果を使用: {schedule_info}, 信頼度: {confidence}")
                return schedule_info
            
            logger.info(f"OpenAI解析の信頼度が低いため、ルールベースのパーサーを使用します: {confidence}")
        except Exception as e:
            logger.error(f"OpenAI API解析エラー: {e}", exc_info=True)
            logger.info("ルールベースのパーサーにフォールバックします")
    
    # ルールベースのパーサーを使用
    return rule_based_parser(text, timezone)

def analyze_conversation_with_openai(conversation_text: str) -> Tuple[Dict[str, Any], float]:
    """
    OpenAIを使用して会話テキストから予定情報を抽出する

    Args:
        conversation_text (str): 解析する会話テキスト

    Returns:
        Tuple[Dict[str, Any], float]: 予定情報と信頼度のタプル
    """
    if not OPENAI_AVAILABLE or not OPENAI_API_KEY:
        logger.warning("OpenAI APIが利用できません。ルールベースのパーサーを使用します。")
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

【分析対象の会話】
{conversation_text}
"""

        # OpenAI APIを使用して会話を解析
        model_to_use = "gpt-3.5-turbo"  # より高性能な "gpt-4" も選択可能
        
        response = openai.chat.completions.create(
            model=model_to_use,
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
        logger.error(f"OpenAIを使用した会話分析でエラー: {e}", exc_info=True)
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
                logger.warning(f"無効な日付形式: {date_str}。デフォルトとして明日を使用します。")
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
                logger.warning(f"無効な開始時間形式: {start_time_str}。デフォルトとして10:00を使用します。")
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
                logger.warning(f"無効な終了時間形式: {end_time_str}。開始時間から1時間後を使用します。")
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
            'confidence': float(result.get("confidence", 0.7))
        }
        
        return schedule_info
    
    except Exception as e:
        logger.error(f"OpenAI結果のスケジュール変換エラー: {e}", exc_info=True)
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

def is_valid_schedule_info(schedule_info: Dict[str, Any]) -> bool:
    """
    スケジュール情報が有効かどうかを確認する基本的なバリデーション
    
    Args:
        schedule_info (Dict[str, Any]): 確認するスケジュール情報
        
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
    
    # 時間の妥当性チェック（終日イベントでなければ）
    if not schedule_info.get('is_all_day') and schedule_info['start_datetime'] >= schedule_info['end_datetime']:
        return False
        
    return True

def ensure_valid_schedule(schedule_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    スケジュール情報が有効であることを確認し、必要に応じて修正する
    
    Args:
        schedule_info (Dict[str, Any]): 確認・修正するスケジュール情報
        
    Returns:
        Dict[str, Any]: 有効なスケジュール情報
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
            tomorrow = now.date() + timedelta(days=1)
            start_datetime = datetime.combine(tomorrow, datetime.min.time().replace(hour=10))
            start_datetime = tz.localize(start_datetime)
        
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
    
    # 開始時間が終了時間より後ならば調整（終日イベントでなければ）
    if not schedule_info.get('is_all_day', False) and 'start_datetime' in schedule_info and 'end_datetime' in schedule_info:
        if schedule_info['start_datetime'] >= schedule_info['end_datetime']:
            schedule_info['end_datetime'] = schedule_info['start_datetime'] + timedelta(hours=1)
    
    # 説明文が長すぎる場合は切り詰める
    if schedule_info.get('description') and len(schedule_info['description']) > 1000:
        schedule_info['description'] = schedule_info['description'][:997] + '...'
    
    return schedule_info

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
            not schedule_info.get('is_all_day') and schedule_info['start_datetime'] >= schedule_info['end_datetime']):
        # 終了時間が開始時間より前の場合、開始から1時間後に設定
        schedule_info['end_datetime'] = schedule_info['start_datetime'] + timedelta(hours=1)
    
    # 終日イベントの場合の時間設定
    if schedule_info.get('is_all_day') and schedule_info.get('start_datetime'):
        start_date = schedule_info['start_datetime'].date()
        schedule_info['start_datetime'] = tz.localize(datetime.combine(start_date, datetime.min.time()))
        schedule_info['end_datetime'] = schedule_info['start_datetime'] + timedelta(days=1)
    
    return schedule_info

def next_weekday(now: datetime, weekday: int, weeks_offset: int = 0) -> datetime.date:
    """
    指定された曜日の次の日付を返す
    
    Args:
        now (datetime): 現在の日時
        weekday (int): 曜日コード（0=月曜, 1=火曜, ..., 6=日曜）
        weeks_offset (int): 週のオフセット（0=今週, 1=来週, ...）
        
    Returns:
        datetime.date: 計算された日付
    """
    days_ahead = weekday - now.weekday()
    if days_ahead <= 0:  # ターゲットの曜日が今日または過去の曜日の場合
        days_ahead += 7
    
    days_ahead += 7 * weeks_offset  # 来週以降の場合
    return now.date() + timedelta(days=days_ahead)

def date_from_month_day(now: datetime, month: int, day: int) -> datetime.date:
    """
    月と日から日付を生成する、年は自動調整
    
    Args:
        now (datetime): 現在の日時
        month (int): 月
        day (int): 日
        
    Returns:
        datetime.date: 計算された日付
    """
    year = now.year
    
    # 指定された月が現在の月より前で、日付も今日より前なら来年と判断
    if month < now.month or (month == now.month and day < now.day):
        year += 1
        
    return datetime(year, month, day).date()

def parse_user_input_for_scheduling(text: str) -> Dict[str, Any]:
    """
    ユーザー入力テキストから予定情報を抽出する
    
    Args:
        text (str): 解析するテキスト
        
    Returns:
        Dict[str, Any]: 予定情報を含む辞書、または解析失敗時はNone
    """
    try:
        logger.info(f"テキストを予定情報に解析します: {text[:100]}...")
        
        # OpenAI APIを使用した解析を試みる
        if OPENAI_AVAILABLE and OPENAI_API_KEY:
            schedule_info, confidence = analyze_conversation_with_openai(text)
            
            # デバッグ情報の出力
            logger.debug(f"OpenAI解析結果 - 信頼度: {confidence}")
            if confidence >= 0.3:
                logger.debug(f"タイトル: {schedule_info.get('title', 'N/A')}")
                logger.debug(f"場所: {schedule_info.get('location', 'N/A')}")
                if 'start_datetime' in schedule_info:
                    logger.debug(f"開始: {schedule_info['start_datetime'].strftime('%Y-%m-%d %H:%M')}")
                if 'end_datetime' in schedule_info:
                    logger.debug(f"終了: {schedule_info['end_datetime'].strftime('%Y-%m-%d %H:%M')}")
            
            # 十分な信頼度があれば、その結果を使用する
            if confidence >= 0.4:
                # 追加のバリデーションを実施
                schedule_info = ensure_valid_schedule(schedule_info)
                return schedule_info
            # 低い信頼度の場合はフォールバックする前に内容をチェック
            elif confidence >= 0.3 and is_valid_schedule_info(schedule_info):
                logger.info("低信頼度の結果を使用します（有効なスケジュールと判断）")
                return schedule_info
        
        # OpenAI APIが使えない、または信頼度が低い場合は簡易パーサーで解析
        logger.info("シンプルなパーサーに切り替えます")
        return rule_based_parser(text)
    
    except Exception as e:
        logger.error(f"解析エラー: {e}", exc_info=True)
        # エラー時はシンプルなパーサーにフォールバック
        try:
            return rule_based_parser(text)
        except Exception:
            logger.error("フォールバックパーサーも失敗しました", exc_info=True)
            return None

def rule_based_parser(text: str, timezone: str = 'Asia/Tokyo') -> Dict[str, Any]:
        """
        ルールベースで予定情報を抽出する

        Args:
            text (str): 予定が含まれるテキスト
            timezone (str): タイムゾーン

        Returns:
            Dict[str, Any]: 予定情報を含む辞書
        """
        # 現在日時を取得
        tz = pytz.timezone(timezone)
        now = datetime.now(tz)
        
        # デフォルト設定
        schedule_info = {
            'title': None,
            'start_datetime': None,
            'end_datetime': None,
            'location': None,
            'description': text,
            'is_all_day': False,
            'confidence': 0.0,
        }
        
        # タイトル抽出の試み
        title_patterns = [
            r'「(.+?)」',  # 「会議」のようなパターン
            r'『(.+?)』',  # 『会議』のようなパターン
            r'について(.+?)する',  # 〜について会議する
            r'(.+?)の打ち合わせ',  # プロジェクトAの打ち合わせ
            r'(.+?)meeting',  # プロジェクトAmeeting
        ]
        
        # タイトル抽出
        for pattern in title_patterns:
            match = re.search(pattern, text)
            if match:
                schedule_info['title'] = match.group(1).strip()
                break
        
        # タイトルがない場合はデフォルト値
        if not schedule_info['title']:
            schedule_info['title'] = '予定'
        
        # 日付・時間の抽出
        # 曜日パターン
        weekday_patterns = {
            '月曜': 0, '月': 0,
            '火曜': 1, '火': 1,
            '水曜': 2, '水': 2,
            '木曜': 3, '木': 3,
            '金曜': 4, '金': 4,
            '土曜': 5, '土': 5,
            '日曜': 6, '日': 6
        }
        
        # 相対日付パターン
        relative_date_patterns = {
            '今日': 0,
            '明日': 1,
            '明後日': 2,
            '来週': 7,
            '再来週': 14
        }
        
        # 時間帯パターン
        time_expression_map = {
            '朝': (7, 0),
            '午前': (10, 0),
            '昼': (12, 0),
            '午後': (14, 0),
            '夕方': (17, 0),
            '夜': (19, 0),
            '夜遅く': (22, 0)
        }
        
        # 日付抽出
        target_date = now.date()
        date_match = None
        
        # 相対日付の解析
        for date_text, days_offset in relative_date_patterns.items():
            if date_text in text:
                target_date = now.date() + timedelta(days=days_offset)
                break
        
        # 週指定の解析
        for weekday_text, weekday_num in weekday_patterns.items():
            if weekday_text in text:
                # 指定された曜日の次の日付を取得
                target_date = next_weekday(now, weekday_num)
                break
        
        # 具体的な日付パターン（YYYY/MM/DD, MM/DD形式）
        date_patterns = [
            r'(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})',  # YYYY/MM/DD
            r'(\d{1,2})[/.-](\d{1,2})',  # MM/DD
        ]
        
        for pattern in date_patterns:
            date_match = re.search(pattern, text)
            if date_match:
                if len(date_match.groups()) == 3:
                    # YYYY/MM/DD形式
                    year, month, day = map(int, date_match.groups())
                    target_date = datetime(year, month, day).date()
                else:
                    # MM/DD形式
                    month, day = map(int, date_match.groups())
                    target_date = date_from_month_day(now, month, day)
                break
        
        # 時間抽出
        start_time = None
        end_time = None
        
        # 時間帯表現の解析
        for time_text, (hour, minute) in time_expression_map.items():
            if time_text in text:
                start_time = time(hour, minute)
                break
        
        # 具体的な時間パターン
        time_patterns = [
            r'(\d{1,2})[:時](\d{2})?',  # 15:30 または 15時
            r'(\d{1,2})時',  # 15時
        ]
        
        for pattern in time_patterns:
            time_match = re.search(pattern, text)
            if time_match:
                hour = int(time_match.group(1))
                minute = int(time_match.group(2)) if time_match.group(2) else 0
                start_time = time(hour, minute)
                break
        
        # タイムゾーンを考慮した日時の作成
        if start_time:
            start_datetime = tz.localize(datetime.combine(target_date, start_time))
            schedule_info['start_datetime'] = start_datetime
            
            # デフォルトの終了時間（1時間後）
            end_datetime = start_datetime + timedelta(hours=1)
            schedule_info['end_datetime'] = end_datetime
        else:
            # 時間が特定できない場合は明日の10時をデフォルトに
            default_start = now.replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(days=1)
            schedule_info['start_datetime'] = default_start
            schedule_info['end_datetime'] = default_start + timedelta(hours=1)
        
        # 信頼度の設定
        confidence = 0.5  # デフォルトの信頼度
        
        # タイトルや日時が明確に特定できた場合は信頼度を上げる
        if schedule_info['title'] and schedule_info['start_datetime']:
            confidence = 0.7
        
        schedule_info['confidence'] = confidence
        
        return schedule_info

# モジュールテスト用のメイン関数
def test_schedule_parsing():
    test_cases = [
        "明日15時に打ち合わせする",
        "来週の月曜日に「プロジェクトA」のミーティングがある",
        "明後日の朝に新入社員研修",
        "2024/03/15に顧客との会議",
        "夕方に部門会議があります"
    ]
    
    for case in test_cases:
        print(f"\n解析テキスト: {case}")
        result = parse_user_input_for_scheduling(case)
        
        if result:
            print("解析結果:")
            for key, value in result.items():
                print(f"{key}: {value}")
        else:
            print("解析に失敗しました")

# スクリプトとして直接実行された場合にテストを実行
if __name__ == "__main__":
    test_schedule_parsing()