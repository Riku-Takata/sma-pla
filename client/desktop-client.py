#!/usr/bin/env python3
"""
スマート予定管理 - オプションのデスクトップクライアント
このスクリプトを実行すると、ネイティブUI通知を表示できます。
Webベースの通知と併用可能です。
"""

import sys
import json
import threading
from PyQt5 import QtWidgets, QtCore, QtGui
from flask import Flask, request, jsonify
import requests

# Flaskサーバー部分
app = Flask(__name__)

class StylishOverlay(QtWidgets.QWidget):
    def __init__(self, event_info, manager):
        super().__init__()
        self.event_info = event_info
        self.manager = manager
        self.initUI()
        
    def initUI(self):
        print(f"DEBUG: StylishOverlay initUI called with: {self.event_info}")
        # フレームレスにしても、今回は境界線を出さないように設定
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint)
        # 透過背景を有効に
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)

        # レイアウト設定（余白ゼロ）
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        
        # 予定情報を取得
        title = self.event_info.get('summary', '')
        date = self.event_info.get('date', '')
        time = self.event_info.get('time', '')
        location = self.event_info.get('location', '')
        
        # 表示用ラベル：グラデーション背景、角丸、ボーダーなし、おしゃれなフォント指定
        text = f"新しい予定:\n"
        text += f"タイトル: {title}\n"
        text += f"日時: {date} {time}"
        if location:
            text += f"\n場所: {location}"
        text += "\n\nGoogleカレンダーに追加しますか？"
        
        label = QtWidgets.QLabel(text)
        label.setAlignment(QtCore.Qt.AlignCenter)
        label.setStyleSheet("""
            QLabel {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff7e5f, stop:1 #feb47b);
                color: white;
                border: none;
                border-radius: 20px;
                padding: 20px;
                font-size: 16pt;
                font-family: "Helvetica Neue", Arial, sans-serif;
            }
        """)
        layout.addWidget(label)
        
        # ボタン配置（水平レイアウト）
        btn_layout = QtWidgets.QHBoxLayout()
        yes_btn = QtWidgets.QPushButton("Yes")
        no_btn = QtWidgets.QPushButton("No")
        yes_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-size: 12pt;
                padding: 10px;
                border: none;
                border-radius: 10px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
        """)
        no_btn.setStyleSheet("""
            QPushButton {
                background-color: #f44336;
                color: white;
                font-size: 12pt;
                padding: 10px;
                border: none;
                border-radius: 10px;
            }
            QPushButton:hover {
                background-color: #da190b;
            }
        """)
        yes_btn.clicked.connect(self.on_yes)
        no_btn.clicked.connect(self.on_no)
        btn_layout.addWidget(yes_btn)
        btn_layout.addWidget(no_btn)
        layout.addLayout(btn_layout)
        
        self.setLayout(layout)
        self.adjustSize()
        
        # ディスプレイの中央に配置
        screen = QtWidgets.QDesktopWidget().screenGeometry()
        size = self.geometry()
        self.move(int((screen.width() - size.width()) / 2),
                 int((screen.height() - size.height()) / 2))
        
        # ドロップシャドウ効果を追加
        shadow = QtWidgets.QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setOffset(0, 0)
        shadow.setColor(QtGui.QColor(0, 0, 0, 160))
        self.setGraphicsEffect(shadow)
        
        # ウィンドウを最前面に持ってくる
        self.raise_()
        self.activateWindow()
        
        print(f"DEBUG: Window geometry: {self.geometry()}")

    def on_yes(self):
        print("DEBUG: on_yes clicked, sending /approve_event")
        try:
            r = requests.post("http://127.0.0.1:5001/approve_event", json=self.event_info, timeout=5)
            print(f"DEBUG: /approve_event response: {r.status_code} {r.text}")
        except Exception as e:
            print(f"ERROR on_yes: {e}")
        self.close()

    def on_no(self):
        print("DEBUG: on_no clicked, sending /deny_event")
        try:
            r = requests.post("http://127.0.0.1:5001/deny_event", json=self.event_info, timeout=5)
            print(f"DEBUG: /deny_event response: {r.status_code} {r.text}")
        except Exception as e:
            print(f"ERROR on_no: {e}")
        self.close()

    def closeEvent(self, event):
        # ウィンドウが閉じられたら、WindowManagerから自身の参照を削除
        self.manager.remove_overlay(self)
        super().closeEvent(event)

class WindowManager(QtCore.QObject):
    def __init__(self):
        super().__init__()
        self.overlays = []  # 生成したオーバーレイウィンドウの参照リスト

    @QtCore.pyqtSlot(dict)
    def show_overlay(self, event_info):
        print(f"DEBUG: show_overlay called with {event_info}")
        overlay = StylishOverlay(event_info, self)
        self.overlays.append(overlay)
        overlay.show()

    def remove_overlay(self, overlay):
        if overlay in self.overlays:
            self.overlays.remove(overlay)
            print(f"DEBUG: Overlay removed; remaining overlays: {len(self.overlays)}")

window_manager = None

@app.route("/new_event", methods=["POST"])
def new_event():
    data = request.get_json()
    print(f"DEBUG: /new_event called with data: {data}")
    if data is None:
        return jsonify({"error": "No data"}), 400
    # UIスレッド上で show_overlay を呼び出す
    QtCore.QMetaObject.invokeMethod(
        window_manager,
        "show_overlay",
        QtCore.Qt.QueuedConnection,
        QtCore.Q_ARG(dict, data)
    )
    return jsonify({"status": "ok"}), 200

def run_flask():
    print("DEBUG: Starting desktop client server on port 5010")
    app.run(port=5010, debug=False, use_reloader=False)

if __name__ == "__main__":
    print("DEBUG: Starting PyQt application")
    qt_app = QtWidgets.QApplication(sys.argv)
    window_manager = WindowManager()
    
    # Flaskサーバーをサブスレッドで起動
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # システムトレイアイコンを追加
    tray_icon = QtWidgets.QSystemTrayIcon(QtGui.QIcon.fromTheme("calendar"))
    tray_icon.setToolTip("スマート予定管理デスクトップクライアント")
    
    # 右クリックメニュー
    tray_menu = QtWidgets.QMenu()
    quit_action = tray_menu.addAction("終了")
    quit_action.triggered.connect(qt_app.quit)
    tray_icon.setContextMenu(tray_menu)
    tray_icon.show()
    
    # 起動通知
    tray_icon.showMessage(
        "スマート予定管理",
        "デスクトップクライアントが起動しました。このクライアントは、Web通知と併用可能です。",
        QtWidgets.QSystemTrayIcon.Information,
        3000
    )
    
    print("デスクトップクライアントを起動しました。システムトレイアイコンから操作できます。")
    sys.exit(qt_app.exec_())