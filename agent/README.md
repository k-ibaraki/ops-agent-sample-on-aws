# ops-agent（エージェント本体）

CloudWatch を自律調査して Slack に通知する運用エージェントの本体です。
AgentCore Runtime 上で動作し、次の 2 経路を扱います。

- 日次チェック: 毎朝、過去 24 時間（設定値）を調査して問題を採点する（`run_daily_check`）
- 調査依頼: Slack から届いた自由文の依頼を調査して回答する（`run_adhoc_investigation`）

どちらもツールは読み取り専用のみで、書き込み系の操作は行いません。
全体像はリポジトリルートの README を参照してください。

## 開発コマンド

```sh
uv sync                # 依存関係のインストール
uv run pytest          # テスト
uv run ruff format .   # フォーマット
uv run ruff check .    # lint
uv run ty check        # 型チェック
```

## 構成

| モジュール | 役割 |
|-----------|------|
| `config.py` | 環境変数からの設定読み込み |
| `models.py` | 調査結果のスキーマ（`DailyReport` / `AdhocReport`、構造化出力用） |
| `aws_tools.py` | CloudWatch 調査ツール群（読み取り専用） |
| `agent.py` | 調査 → 構造化出力 → 通知のオーケストレーション（日次 / 依頼の両方） |
| `prompts.py` | プロンプトの組み立て（設定値の埋め込み・スキルの読み込み指示・インジェクション対策） |
| `report.py` | 通知メッセージの整形（回答・失敗通知を含む） |
| `notifier.py` | SNS への発行 |
| `main.py` | AgentCore Runtime のエントリポイント（ペイロードで 2 経路を振り分け） |
| `skills/` | 調査の方針・採点基準・レポート書式（Strands Skills）。`investigation-policy` / `scoring-rubric` / 書式 2 種の計 4 種 |
