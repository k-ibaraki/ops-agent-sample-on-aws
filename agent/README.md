# ops-agent（エージェント本体）

過去 24 時間の CloudWatch を自律調査して Slack に通知する運用エージェントの本体です。
AgentCore Runtime 上で動作します。全体像はリポジトリルートの README を参照してください。

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
| `models.py` | 採点結果のスキーマ（構造化出力用） |
| `aws_tools.py` | CloudWatch 調査ツール群（読み取り専用） |
| `agent.py` | 調査 → 採点 → 通知のオーケストレーション |
| `report.py` | 通知メッセージの整形 |
| `notifier.py` | SNS への発行 |
| `main.py` | AgentCore Runtime のエントリポイント |
