# NoMo: 自然な会話から予定を自動管理するスマートスケジュールシステム

## 🚀 プロダクト概要
![スクリーンショット 2025-03-01 124512](https://github.com/user-attachments/assets/af9a3692-bba3-40be-8f1b-1b0decb78ba0)

NoMoは、チャットプラットフォーム上の自然な会話から予定を自動的に検出し、Googleカレンダーに登録する革新的なスケジュール管理システムです。面倒な手動入力を排除し、予定の見逃しやダブルブッキングを防ぐソリューションを提供します。

### 💡 課題とソリューション

**課題:**
- 予定管理ツールの手動入力の煩わしさ
- 予定の把握し忘れ
- ダブルブッキングの発生
- 複数のコミュニケーションプラットフォーム間での予定同期の困難さ

**NoMoのソリューション:**
- チャットメッセージから自動的に予定を抽出
- 自然言語処理と機械学習による高精度な予定検出
- マルチプラットフォーム対応（Slack、LINE、Discord、Microsoft Teams）
- Google Calendarとのシームレスな連携
- リアルタイムの予定重複検知と代替案の提示

## 🌟 主な機能

1. **自動予定検出**
   - チャットメッセージから日時、場所、タイトルを自動抽出
   - OpenAI GPTを活用した高度な自然言語処理
   - 曖昧な表現（「来週の月曜日」「夕方」など）の柔軟な解釈

2. **マルチプラットフォーム対応**
   - Slack、LINE、Discord、Microsoft Teamsに対応
   - 各プラットフォームのAPIと連携
   - プラットフォーム横断的な予定管理

3. **インテリジェントな予定登録**
   - ダブルブッキング自動検知
   - 重複時に代替時間を自動提案
   - ユーザー承認フロー

4. **リアルタイム通知システム**
   - デスクトップ通知
   - ブラウザ通知センター
   - WebSocket・Redisによるリアルタイム更新

## 🔧 技術スタック

### バックエンド
- Python 3.12
- Flask
- SQLAlchemy
- Google Calendar API
- OpenAI API
- Redis

### フロントエンド
- JavaScript (Socket.IO)
- PyQt5（デスクトップクライアント）
- WebSocket

### インフラ
- Docker
- Docker Compose
- ngrok
- Gunicorn

## 📊 技術的な革新性

1. **自然言語処理**
   - OpenAI GPTを活用した高度な予定情報抽出
   - マルチモーダルな言語理解
   - コンテキストを考慮した予定解析

2. **モジュラーアーキテクチャ**
   - プラグイン型のプラットフォーム対応
   - 拡張性の高いマイクロサービス設計
   - 各コンポーネントの疎結合

3. **耐障害性**
   - フォールバックメカニズム
   - 自動再接続
   - 分散システムの堅牢性

## 🎯 ターゲットユーザー

- 多忙なビジネスパーソン
- リモートワーカー
- チーム協働に依存する組織
- 複数のコミュニケーションツールを利用するユーザー

## 🚀 今後の展望

- より多くのメッセージングプラットフォームのサポート
- 機械学習モデルの継続的な改善
- カスタマイズ可能な予定抽出ルール
- エンタープライズ向けの高度な機能

## 🏆 ハッカソンでの挑戦

本プロジェクトでは、以下の技術的チャレンジに取り組みました：

1. 複雑な自然言語処理
2. マルチプラットフォーム統合
3. リアルタイム分散システムの設計
4. 高い拡張性と保守性の実現

## インストールと使用方法

### 必要な環境
- Docker
- Docker Compose
- Python 3.12+
- ngrok アカウント

### セットアップ手順
```bash
# リポジトリをクローン
git clone https://github.com/yourusername/nomo-schedule-manager.git
cd nomo-schedule-manager

# .env.exampleを.envにコピーし、必要な設定を行う
cp .env.example .env

# Docker Composeで起動
./startup.py
```

## ライセンス

MIT License

## 開発チーム

NoMo Development Team - 予定管理の未来を創造する革新者たち

---

🌈 **NoMo: 予定管理を、より自然に、よりスマートに。**
