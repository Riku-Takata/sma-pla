# gunicorn.conf.py
import os

# サーバー設定
bind = '0.0.0.0:5002'
workers = 1
worker_class = 'eventlet'
timeout = 120

# ログ設定
accesslog = '-'  # stdout
errorlog = '-'   # stderr
loglevel = 'info'

# 環境変数から追加の設定を取得
port = int(os.getenv('PORT', 5002))
bind = f'0.0.0.0:{port}'