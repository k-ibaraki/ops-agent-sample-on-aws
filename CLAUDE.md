# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## プロジェクト概要

AWS アカウントの日次ヘルスチェックを行う運用エージェントのサンプル。毎朝定時に Bedrock AgentCore 上のエージェント（Strands Agents / Python）が起動し、過去 24 時間の CloudWatch を自律調査して、問題を 0〜100 点で採点し SNS 経由で Slack に通知する。加えて、Slack から自由文で追加調査を依頼できる（調査と報告のみ。書き込み系の実作業は行わない）。

処理の流れ:

```
EventBridge Scheduler（毎朝 8:00 JST）      ┐
                                            ├→ 中継 Lambda（invoker/handler.py）
Slack（@Amazon Q run ops "..."）           ┘     → AgentCore Runtime（agent/、Strands Agents）
                                                     ├─ CloudWatch 調査ツール群（読み取り専用）
                                                     └─ 採点結果を構造化出力 → 整形 → SNS Publish
                                                          → Amazon Q Developer in chat applications
                                                            （旧 AWS Chatbot）→ Slack
```

2 経路の違い: スケジュール起動は同期（`InvokeAgentRuntime` の完了を待つ）、Slack からの依頼は
`--invocation-type Event` による非同期。中継 Lambda はペイロードの `trigger`（`scheduled` /
`adhoc`）で振り分ける。

設計決定の経緯は docs/DESIGN.md（決定事項の一覧表）、実装時のつまずきは docs/implementation-log.md に記録されている。設計変更時はこれらも更新する。

## コマンド

### 初回セットアップ

```sh
mise install                        # Node.js / pnpm / uv を導入
pnpm install                        # CDK 側の依存関係
cp parameter.sample.ts parameter.ts # デプロイパラメータ（gitignore 済み・build/synth に必須）
cd agent && uv sync                 # エージェント側の依存関係
```

### Python（agent/ ディレクトリで実行）

```sh
uv run pytest                          # 全テスト
uv run pytest tests/test_agent.py      # ファイル単位
uv run pytest -k "テスト名の一部"        # 単一テスト
uv run ruff format .                   # フォーマット
uv run ruff check .                    # lint
uv run ty check                        # 型チェック
```

### CDK（リポジトリルートで実行）

```sh
pnpm build                  # 型チェック (tsc)
pnpm test                   # jest（Template アサーション）
pnpm test -- -t "テスト名"   # 単一テスト
pnpm cdk synth --quiet      # synth 確認
pnpm cdk deploy             # デプロイ
```

### コミット前チェック（必須）

コミット前に必ず以下をすべて通すこと（CI（.github/workflows/ci.yml）と同じ内容）:

```sh
# agent/ ディレクトリで
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest

# リポジトリルートで
pnpm build
pnpm test
```

## アーキテクチャ

3 つのコンポーネントが 1 リポジトリに同居し、それぞれ言語・ツールチェーンが異なる。

| 場所 | 役割 | 技術 |
|------|------|------|
| ルート + `stacks/` + `test/` | インフラ定義 | CDK (TypeScript) / jest |
| `agent/` | エージェント本体 | Python 3.14 / uv / Strands Agents / pytest |
| `invoker/` | 中継 Lambda | Python 3.14（依存なし、boto3 は Lambda ランタイム同梱） |

### エージェント側（agent/src/ops_agent/）の設計原則

LLM に任せる範囲を意図的に絞っている。この分離はセキュリティ設計（プロンプトインジェクション対策）でもあるため、変更時も維持すること:

- **LLM が扱うのは調査と採点だけ**: `agent.py` の `run_daily_check()` / `run_adhoc_investigation()` が「調査（自律ツール使用）→ `structured_output()` で `DailyReport` / `AdhocReport`（Pydantic、`models.py`）に結果を受け取る」を実行する
- **通知の整形と SNS 発行は LLM を介さない通常のコード**: `report.py`（Amazon Q Developer カスタム通知形式への整形、JST 変換）と `notifier.py`。純粋関数なのでテスト可能。Slack への回答に載る依頼文は、LLM の出力ではなく受け取った文字列をそのまま使う
- **ツールは読み取り専用のみ**: `aws_tools.py` は boto3 の薄いラッパー 8 種。対象リージョンを `Config.target_regions` の許可リストで検証し、調査期間は `Config.max_lookback_hours` で頭打ちにする（`_resolve_hours()` が唯一の経路）。書き込み系の権限・ツールをエージェントに追加しない方針
- **静的な方針・基準は Strands Skills で誘導**: `skills/` 配下の 4 スキル（調査の方針・手順 = `investigation-policy`、採点基準 = `scoring-rubric`、レポート書式 = `slack-report-style` / `adhoc-report-style`）。`AgentSkills` には `skills/` ごと渡してすべて読み込ませる。スキル本文は保証注入ではないため、読み込みはプロンプト側で明示指示する
- **プロンプトの組み立ては `prompts.py` に集約**: 設定値の埋め込み・スキルの読み込み指示・インジェクション対策のガード文言など、必ずモデルに届けたい要素はスキルではなくここに置く（`agent.py` はオーケストレーション専任）
- **Slack からの依頼は非同期なので失敗も必ず通知する**: `run_adhoc_investigation()` は例外時に失敗通知を発行してから再送出する（依頼者を待たせ続けないため）
- **設定は環境変数経由**: `config.py` の `Config.from_env()`。環境変数は CDK スタック（`stacks/ops-agent-stack.ts` の `environmentVariables`）が設定するため、設定項目の追加時は両側の変更が必要
- **時刻の扱い**: 調査・推論は UTC のまま、最終レポートは JST 変換して「JST」を明記（システムプロンプトと `report.py` の両方に規定がある）

### CDK 側の特徴

- スタックはクラス継承ではなく関数ベース（`createOpsAgentStack()`）で定義する
- デプロイパラメータは型付きの `parameter.ts` に集約（CDK context は使わない）。`parameter.ts` は gitignore されており、CI では `parameter.sample.ts` からコピーして生成する。パラメータ追加時は `parameter.sample.ts`・`OpsAgentParameters` 型・README の設定一覧表を揃えて更新する
- AgentCore Runtime は `aws_bedrockagentcore` の L2 construct で、`agent/` の Dockerfile を linux/arm64 でビルドしてデプロイする
- IAM はエージェントの調査ツールが使う読み取り API のみの最小権限。ツール追加時は `runtime.grant()` のアクション一覧も更新する
- 中継 Lambda と Scheduler はリトライ 0 回に設定（エージェントの多重実行 = 重複通知を防ぐため）。この設定を変えないこと
- Slack 連携を作る場合、チャネルロールとガードレールポリシーの両方を中継 Lambda の `lambda:InvokeFunction` だけに絞る。ガードレールは未指定だと AdministratorAccess が既定になるため省略しないこと

### テストの構成

- Python のテストは `agent/tests/` に集約。`invoker/` のテスト（`test_invoker.py`）も pyproject.toml の `pythonpath = ["src", "../invoker"]` により agent/ の pytest から実行される
- boto3 クライアントはフェイク注入で差し替える方式（各ツール関数の `client_factory` 引数、`run_daily_check()` の `agent` / `sns_client` 引数）。moto 等のモックライブラリは使わない
- CDK のテストは jest の Template アサーション

## 開発の進め方

- テスト駆動開発（TDD）: レッド（テスト先行で失敗を確認）→ グリーンの順に進める
- ドキュメント・コメント・コミットメッセージは日本語で統一
- ruff の RUF001/RUF002/RUF003 は日本語の全角記号を誤検知するため無効化している（再有効化しない）
