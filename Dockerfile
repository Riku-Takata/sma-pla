# ベースイメージ (Python 3.10を例示)
FROM python:3.10-slim

# アプリの作業ディレクトリ
WORKDIR /app

# パッケージインストールに必要なファイルのみ先行コピー
COPY requirements.txt .

# ライブラリのインストール
RUN pip install --no-cache-dir -r requirements.txt

# アプリ本体をコピー
COPY . .

# Flaskのポート
EXPOSE 5000

# 環境変数が必要ならDockerfile上でも設定可能 (例: デバッグ用)
# ENV DEBUG=true

# コンテナ起動時に実行されるコマンド
CMD ["python", "app.py"]
