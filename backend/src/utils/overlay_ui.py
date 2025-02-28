# utils/overlay_ui.py
import sys
import json
import threading
from PyQt5 import QtWidgets, QtCore, QtGui
import requests

class StylishOverlay(QtWidgets.QWidget):
    def __init__(self, event_info, manager):
        super().__init__()
        self.event_info = event_info
        self.manager = manager  # WindowManagerへの参照
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
        
        # 固定の座標で配置（例として(100,100)）
        self.move(100, 100)
        
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

# グローバル変数
window_manager = None
_qt_app = None

def init_overlay_app():
    """オーバーレイアプリケーションを初期化する"""
    global _qt_app, window_manager
    
    if _qt_app is None:
        # PyQtアプリケーションの初期化
        _qt_app = QtWidgets.QApplication(sys.argv)
        window_manager = WindowManager()
        
        # アプリケーションが終了しないようにする
        # ウィンドウが全て閉じられても、アプリケーションは終了しない
        _qt_app.setQuitOnLastWindowClosed(False)
        
        # 別スレッドでQtのイベントループを実行
        qt_thread = threading.Thread(target=lambda: _qt_app.exec_(), daemon=True)
        qt_thread.start()
        
    return window_manager

def show_event_overlay(event_info):
    """イベント情報をオーバーレイウィンドウで表示する"""
    global window_manager
    
    if window_manager is None:
        window_manager = init_overlay_app()
    
    # UIスレッド上で show_overlay を呼び出す
    QtCore.QMetaObject.invokeMethod(
        window_manager,
        "show_overlay",
        QtCore.Qt.QueuedConnection,
        QtCore.Q_ARG(dict, event_info)
    )
    return True