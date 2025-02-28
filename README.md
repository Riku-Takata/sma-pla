# スマート予定管理ボット

Slack上の会話から予定を自動的に検出し、Googleカレンダーに登録するボットアプリケーションです。

## 特徴

- Slack上の会話から予定情報を自動的に抽出
- Google Calendarとの連携による予定の自動登録
- ダブルブッキングの検出と代替時間の提案
- 自然言語処理による柔軟な予定認識

## 技術スタック

- Python 3.12
- Flask (Webフレームワーク)
- SQLAlchemy (ORM)
- Slack API
- Google Calendar API
- OpenAI API (オプション) - 自然言語解析の拡張
- Docker & Docker Compose
- ngrok (開発環境でのトンネリング)

## Dockerを使用したセットアップ（推奨）

### 1. リポジトリをクローン

```bash
git clone https://github.com/yourusername/smart-schedule-bot.git
cd smart-schedule-bot
```

### 2. 環境変数の設定

`.env.example`ファイルを`.env`にコピーし、必要な環境変数を設定します。

```bash
cp .env.example .env
# .envファイルを編集して各API情報を入力
```

### 3. Dockerを使ってアプリケーションを起動

#### macOS/Linux:
```bash
# 実行権限を付与
chmod +x startup.sh

# スタートアップスクリプトを実行
./startup.sh
```

#### Windows:
```
# スタートアップバッチファイルを実行
start.bat
```

または直接PowerShellで:
```powershell
powershell -ExecutionPolicy Bypass -File startup.ps1
```

スクリプトはngrokのURLを自動的に取得し、Slack APIの設定に必要なURLを表示します。

### 4. Slack Appの設定

スクリプトが表示したURLを以下の設定に使用します：

1. **Event Subscriptions URL**:
   - `https://NGROK_URL/webhook/slack/events`

2. **Slash Commands URL**:
   - `https://NGROK_URL/webhook/slack/command`

3. **Interactive Components URL**:
   - `https://NGROK_URL/webhook/slack/interactive`

### 5. Google OAuth設定

1. Google Cloud Consoleで認証情報を設定
2. リダイレクトURIに `https://NGROK_URL/oauth/google/callback` を追加

## 手動セットアップ

### 1. 依存パッケージのインストール

```bash
pip install -r requirements.txt
```

### 2. ngrokを起動

```bash
ngrok http 5001
```

### 3. 環境変数を設定

```bash
# .envファイルの設定
FRONTEND_URL=https://NGROK_URL
GOOGLE_REDIRECT_URI=https://NGROK_URL/oauth/google/callback
```

### 4. アプリケーション起動

```bash
flask run --host=0.0.0.0 --port=5001
```

## Slack Appの設定手順

1. [Slack API Dashboard](https://api.slack.com/apps)にアクセス
2. **Create New App** > **From scratch**をクリック
3. アプリ名とワークスペースを選択
4. 以下の設定を行います：

   **a. OAuth & Permissions**
   - Bot Token Scopesに以下を追加：
     - `channels:history`
     - `chat:write`
     - `commands`
     - `groups:history`
     - `im:history`
     - `users:read`
     - `users:read.email`

   **b. Slash Commands**
   - Command: `/plan`
   - Request URL: `https://NGROK_URL/webhook/slack/command`
   - Description: "会話から予定を検出してGoogleカレンダーに追加"

   **c. Event Subscriptions**
   - Request URL: `https://NGROK_URL/webhook/slack/events`
   - Subscribe to bot events: `message.channels`, `message.groups`, `message.im`

   **d. Interactivity & Shortcuts**
   - Request URL: `https://NGROK_URL/webhook/slack/interactive`

5. ワークスペースにアプリをインストール

## 使い方

### Slack上での使用方法

1. ワークスペース上で `/plan` コマンドを入力
2. 初回利用時はGoogleアカウントとの連携が必要です（表示されるリンクから認証）
3. 会話履歴から自動的に予定が検出され、確認ダイアログが表示されます
4. 確認後、Googleカレンダーに予定が登録されます
5. 予定の重複がある場合は通知され、代替の時間を提案します

具体例:

```
あなた: 明日の15時からプロジェクトMTGやりましょう
同僚: 了解です、場所は会議室でよいですか？
あなた: はい、Aルームで
あなた: /plan
```

上記の会話から自動的に「プロジェクトMTG」の予定を検出し、カレンダーに登録します。

## トラブルシューティング

### ngrokのURLが取得できない場合

```bash
# ngrokのAPIに直接アクセス
curl http://localhost:4040/api/tunnels

# または、Docker内のngrokコンテナ内部から確認
docker-compose exec ngrok /bin/sh -c 'curl http://localhost:4040/api/tunnels'
```

### Slackイベント検証に失敗する場合

1. ngrokのURLが正しく設定されているか確認
2. Flaskアプリケーションのログを確認：
   ```bash
   docker-compose logs -f web
   ```
3. イベントペイロードを確認：
   ```bash
   # slack_handler.pyの/eventsエンドポイントにデバッグログを追加
   ```
