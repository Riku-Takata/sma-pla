#!/usr/bin/env python3
"""
スマート予定管理 - デスクトップクライアント
SlackコマンドをトリガーにしたオーバーレイUIを提供
"""

import sys
import json
import threading
import os
from datetime import datetime
from PyQt5 import QtWidgets, QtCore, QtGui
from flask import Flask, request, jsonify
import requests
import logging

# ロギングの設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('desktop_client.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 設定ファイルの読み込み
def load_config():
    """設定ファイルからバックエンドURLを読み込む"""
    try:
        # .envファイルから環境変数を読み込む
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        logger.warning("python-dotenvがインストールされていません")
    
    # デフォルトURLを設定
    default_backend_url = "http://localhost:5001"
    backend_url = os.getenv("BACKEND_URL", default_backend_url)
    return {
        "backend_url": backend_url,
        "desktop_port": int(os.getenv("DESKTOP_CLIENT_PORT", 5010))
    }

CONFIG = load_config()

class StylishOverlay(QtWidgets.QWidget):
    def __init__(self, event_info, manager):
        super().__init__()
        self.event_info = event_info
        self.manager = manager
        self.initUI()
        
    def initUI(self):
        logger.info(f"イベント情報: {self.event_info}")
        
        # ウィンドウのスタイル設定
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint | 
                            QtCore.Qt.WindowStaysOnTopHint | 
                            QtCore.Qt.Tool)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        
        # メインレイアウト
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)
        
        # イベント情報の取得
        title = self.event_info.get('summary', '予定')
        date = self.event_info.get('date', '')
        time = self.event_info.get('time', '')
        location = self.event_info.get('location', '')
        
        # カード用のウィジェット
        card = QtWidgets.QWidget()
        card.setStyleSheet("""
            QWidget {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, 
                            stop:0 rgba(255, 126, 95, 220), 
                            stop:1 rgba(254, 180, 123, 220));
                color: white;
                border-radius: 15px;
                padding: 20px;
            }
        """)
        card_layout = QtWidgets.QVBoxLayout()
        
        # タイトル
        title_label = QtWidgets.QLabel(f"新しい予定: {title}")
        title_label.setStyleSheet("""
            font-size: 18px;
            font-weight: bold;
            margin-bottom: 10px;
        """)
        card_layout.addWidget(title_label)
        
        # 詳細情報
        details_text = f"日時: {date} {time}"
        if location:
            details_text += f"\n場所: {location}"
        
        details_label = QtWidgets.QLabel(details_text)
        details_label.setStyleSheet("""
            font-size: 14px;
        """)
        card_layout.addWidget(details_label)
        
        # ボタンレイアウト
        button_layout = QtWidgets.QHBoxLayout()
        
        approve_btn = QtWidgets.QPushButton("承認")
        approve_btn.setStyleSheet("""
            background-color: #4CAF50;
            color: white;
            border: none;
            padding: 10px;
            border-radius: 5px;
            font-weight: bold;
        """)
        approve_btn.clicked.connect(self.on_approve)
        
        deny_btn = QtWidgets.QPushButton("拒否")
        deny_btn.setStyleSheet("""
            background-color: #f44336;
            color: white;
            border: none;
            padding: 10px;
            border-radius: 5px;
            font-weight: bold;
        """)
        deny_btn.clicked.connect(self.on_deny)
        
        button_layout.addWidget(approve_btn)
        button_layout.addWidget(deny_btn)
        
        card_layout.addLayout(button_layout)
        card.setLayout(card_layout)
        
        layout.addWidget(card)
        self.setLayout(layout)
        
        # 画面中央に配置
        self.adjustSize()
        screen = QtWidgets.QDesktopWidget().screenGeometry()
        size = self.geometry()
        self.move(
            int((screen.width() - size.width()) / 2),
            int((screen.height() - size.height()) / 2)
        )
        
        # ドロップシャドウ効果
        shadow = QtWidgets.QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QtGui.QColor(0, 0, 0, 160))
        shadow.setOffset(0, 0)
        self.setGraphicsEffect(shadow)
        
    def on_approve(self):
        logger.info("予定を承認します")
        try:
            # バックエンドに承認リクエストを送信
            response = requests.post(
                f"{CONFIG['backend_url']}/api/events/{self.event_info.get('event_id')}/approve", 
                json=self.event_info
            )
            response.raise_for_status()
            logger.info("予定の承認に成功しました")
        except Exception as e:
            logger.error(f"予定承認エラー: {e}")
        finally:
            self.close()
    
    def on_deny(self):
        logger.info("予定を拒否します")
        try:
            # バックエンドに拒否リクエストを送信
            response = requests.post(
                f"{CONFIG['backend_url']}/api/events/{self.event_info.get('event_id')}/deny", 
                json=self.event_info
            )
            response.raise_for_status()
            logger.info("予定の拒否に成功しました")
        except Exception as e:
            logger.error(f"予定拒否エラー: {e}")
        finally:
            self.close()

class WindowManager(QtCore.QObject):
    notification_received = QtCore.pyqtSignal(dict)
    
    def __init__(self):
        super().__init__()
        self.overlays = []
        self.notification_received.connect(self.show_overlay)
    
    def show_overlay(self, event_info):
        logger.info(f"オーバーレイを表示: {event_info}")
        overlay = StylishOverlay(event_info, self)
        self.overlays.append(overlay)
        overlay.show()
    
    def remove_overlay(self, overlay):
        if overlay in self.overlays:
            self.overlays.remove(overlay)
            logger.info(f"オーバーレイを削除。残りのオーバーレイ数: {len(self.overlays)}")

class DesktopNotificationServer:
    def __init__(self, window_manager):
        self.app = Flask(__name__)
        self.window_manager = window_manager
        self.setup_routes()
    
    def setup_routes(self):
        @self.app.route("/event", methods=["POST"])
        def receive_event():
            try:
                event_data = request.get_json()
                logger.info(f"イベント受信: {event_data}")
                
                # メインスレッドでシグナルを発行
                QtCore.QMetaObject.invokeMethod(
                    self.window_manager, 
                    'notification_received', 
                    QtCore.Qt.QueuedConnection, 
                    QtCore.Q_ARG(dict, event_data)
                )
                
                return jsonify({"status": "ok"}), 200
            except Exception as e:
                logger.error(f"イベント処理エラー: {e}")
                return jsonify({"status": "error", "message": str(e)}), 500
    
    def run(self):
        logger.info(f"デスクトップ通知サーバーを起動: {CONFIG['desktop_port']}")
        self.app.run(port=CONFIG['desktop_port'], host='0.0.0.0', debug=False, use_reloader=False)

def main():
    qt_app = QtWidgets.QApplication(sys.argv)
    
    # システムトレイ
    tray_icon = QtWidgets.QSystemTrayIcon(
        QtGui.QIcon.fromTheme("calendar", QtGui.QIcon(QtGui.QPixmap(32, 32)))
    )
    tray_icon.setToolTip("スマート予定管理 デスクトップクライアント")
    
    # トレイメニュー
    tray_menu = QtWidgets.QMenu()
    exit_action = tray_menu.addAction("終了")
    exit_action.triggered.connect(qt_app.quit)
    tray_icon.setContextMenu(tray_menu)
    tray_icon.show()
    
    # ウィンドウマネージャとFlaskサーバーの初期化
    window_manager = WindowManager()
    notification_server = DesktopNotificationServer(window_manager)
    
    # デーモンスレッドでFlaskサーバーを起動
    server_thread = threading.Thread(
        target=notification_server.run, 
        daemon=True
    )
    server_thread.start()
    
    # ブラウザ通知の許可を求める
    tray_icon.showMessage(
        "デスクトップクライアント起動",
        "Slackから予定を検出したら通知します。",
        QtWidgets.QSystemTrayIcon.Information,
        3000
    )
    
    # アプリケーションの実行
    sys.exit(qt_app.exec_())

if __name__ == "__main__":
    main()