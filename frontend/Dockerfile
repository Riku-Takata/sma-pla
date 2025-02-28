FROM python:3.12-slim

# 作業ディレクトリを設定
WORKDIR /app

# 必要なシステムパッケージをインストール
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# requirements.txtをコピーして依存関係をインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリケーションコードをコピー
COPY . .

# ポートを公開
EXPOSE 5002

# 環境変数のデフォルト値を設定
ENV REDIS_URL=redis://redis:6379/0
ENV BACKEND_URL=http://backend:5001
ENV PORT=5002

# アプリケーションの起動コマンド
CMD ["python", "server.py"]