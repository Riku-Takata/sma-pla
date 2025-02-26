# ビルドステージ
FROM python:3.12-slim AS builder

# 作業ディレクトリを設定
WORKDIR /app

# システムパッケージを更新し、必要最低限のツールをインストール
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# requirements.txtをコピーして依存関係をインストール
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# 最終ステージ（軽量イメージ）
FROM python:3.12-slim

# 作業ディレクトリを設定
WORKDIR /app

# ビルドステージからインストール済みのライブラリをコピー
COPY --from=builder /root/.local /root/.local

# アプリのソースコードをコピー
COPY . .

# PATHを更新してpipインストールしたものを利用可能に
ENV PATH=/root/.local/bin:$PATH

# Flaskのデフォルトポートを公開
EXPOSE 5001

# 本番環境向けにGunicornを使用（開発時はコメントアウト可能）
CMD ["gunicorn", "--bind", "0.0.0.0:5001", "app:app"]

# 開発用にFlaskのビルトインサーバーを使いたい場合は以下をCMDとして使用
# CMD ["flask", "run", "--host=0.0.0.0"]