# 実装記録（Implementation Log）

実装時の作業内容とつまずきの記録です。設計決定そのものは [DESIGN.md](DESIGN.md) を参照してください。

## 2026-08-17: 初期実装

### 進め方

テスト駆動開発（TDD）で、レッド（テスト先行で失敗を確認）→ グリーン（実装してテストを通す）の順に進めた。

1. 設計決定録（DESIGN.md）を作成
2. `agent/` を uv で scaffold（Python 3.14 / ruff / ty / pytest）
3. エージェント本体をテスト先行で実装
   - `config.py`: 環境変数からの設定読み込み
   - `models.py`: 採点結果の Pydantic スキーマ（構造化出力用）
   - `aws_tools.py`: CloudWatch 調査ツール 5 種（読み取り専用、リージョン許可リスト検証つき）
   - `report.py`: カスタム通知形式（Amazon Q Developer in chat apps）への整形
   - `notifier.py`: SNS 発行
   - `agent.py`: 調査 → 構造化出力 → 通知のオーケストレーション
   - `main.py`: BedrockAgentCoreApp のエントリポイント
4. 中継 Lambda（`invoker/handler.py`）をテスト先行で実装
5. CDK スタックを jest の Template アサーション先行で実装
6. CI（GitHub Actions）・README を整備

### 実装上の判断・つまずき

- Python バージョン: 当初 3.13 で始めたが、CDK の `lambda.Runtime.PYTHON_3_14` 対応を確認できたため、venv / Docker / Lambda / CI とも 3.14 に統一した
- ruff の RUF001/RUF002/RUF003 は日本語の全角記号（（）や 〜 など）を誤検知するため、無効化した
- ty が `invoker/` のモジュールを解決できなかったため、`[tool.ty.environment] extra-paths` に `../invoker` を追加した
- boto3 クライアントはテストでフェイクを注入できるよう、各ツール関数に `client_factory` 引数を持たせた（moto などの追加依存を避けるため）
- AgentCore Runtime の L2 construct が自動生成する実行ロールには、Runtime 自身の実行ログ書き込み権限（`/aws/bedrock-agentcore/runtimes/*` にスコープ済み）が含まれる。当初「`logs:PutLogEvents` が存在しないこと」を最小権限のテストとしていたが誤検知だったため、「自分で付与した読み取り専用ステートメントの中身が正しいこと」を検証する形に修正した
- Strands のツールは `@tool(name=...)` を付けた closure として `build_tools()` で生成し、設定（Config）を束縛した。ロジック本体は素の関数として公開し、単体テストは素の関数側に対して行う構成にした
- CDK のディレクトリは `lib/`（cdk init のデフォルト）から `stacks/` にリネームした

### 検証結果

- Python: pytest 23 件パス / ruff・ty クリーン
- CDK: jest 10 件パス / `cdk synth` 成功
- Docker イメージのビルドとデプロイは未実施（デプロイ時に検証する）

## 2026-08-17: パラメータを parameter.ts に集約

当初は CDK context（`-c` オプション）でパラメータを渡す設計だったが、
設定をファイルで管理したいという要望を受けて、リポジトリ直下の型付き `parameter.ts` に集約した。

- `OpsAgentParameters` インターフェースをスタック側で定義し、スタックには props として渡す
- context 参照（`node.tryGetContext`）は廃止。設定の入口が 1 箇所になり、型チェックも効く
- テストも context 経由ではなく `Partial<OpsAgentParameters>` の上書きで書けるようになり、シンプルになった
- `.env.example` 方式を採用: `parameter.sample.ts` をコミットし、実際の `parameter.ts` は gitignore。
  個人環境の値（Slack の ID など）の誤コミットを防ぐ。テストは `parameter.sample.ts` を参照し、
  CI では `cp parameter.sample.ts parameter.ts` を挟んで build / synth を通す

## 2026-08-17: セルフレビューでの修正

5観点（コード品質・テスト・セキュリティ・ドキュメント・規約）のセルフレビューを実施し、以下を修正した。

- `TARGET_REGIONS` 未指定時にコンテナの `AWS_REGION` 環境変数へ暗黙依存していたため、
  CDK 側で常に明示的に設定する形に変更（未指定ならデプロイ先リージョン）
- README の `aws lambda invoke` 例が AWS CLI v2 で失敗するため
  `--cli-binary-format raw-in-base64-out` を追加
- `query_logs` のタイムアウト経路（`stop_query` 呼び出し）のテストを追加
- ログ本文経由のプロンプトインジェクションに関する注意書きと設計上の緩和策を README に追記

## 2026-08-17: 初回デプロイでの検証と修正

東京リージョンへ実際にデプロイして動作確認を行い、ユニットテストでは検出できない問題を 3 つ見つけて修正した。

1. コンテナ起動失敗（`ModuleNotFoundError: No module named 'ops_agent'`）:
   `pyproject.toml` に `[build-system]` が無く、uv がプロジェクト自身をパッケージとして
   インストールしていなかった。ローカルの pytest は `pythonpath = ["src"]` 設定で
   動いていたため気づけなかった。`uv_build` バックエンドを追加して解決。
   あわせて `.dockerignore` の README.md 除外がビルド失敗（`readme` 参照）を起こすため解除
2. エージェントの多重実行（重複通知）: 中継 Lambda の boto3 クライアントの
   read timeout がデフォルト 60 秒で、エージェントの調査時間（2〜3 分）より短く、
   botocore の自動リトライで同じ日次チェックが 3 回実行された。
   `read_timeout=840` + リトライ無効化（`total_max_attempts: 1`）で解決。
   Lambda の非同期リトライ（`configureAsyncInvoke`）と Scheduler のリトライも 0 に設定
3. Slack 表示の崩れ: Slack の mrkdwn は太字 `*...*` の外側に全角文字（`（` など）が
   隣接すると太字にならない。太字を使う行は「行全体を `*...*` で包む」レイアウトに変更し、
   これを保証するテストを追加

検証結果: 手動実行 1 回で調査 → 採点 → Slack 通知まで完走（約 2 分 20 秒・実行回数 1 回）。

教訓: Docker イメージのビルドとコンテナ内での import 確認は、デプロイ前にローカルの
`docker build` + `docker run` でスモークテストしておくと手戻りが少ない。

## 2026-08-17: レポート文面の書式を Strands Skills で誘導

Slack 通知の文面が長文の塊になり読みづらいというフィードバックを受けて改善した。

- 構造（見出し・スコア行・区切り・段落の余白）は引き続き `report.py` の決定的整形で担保。
  状況・推奨は「ラベル行 + 本文」の段落レイアウトに変更
- 文面そのもの（1 文ごとの改行・推奨アクションの番号付き箇条書き・文体）は
  Strands の Skills 機能で `slack-report-style` スキル（SKILL.md）として定義し、
  `AgentSkills` プラグインでエージェントに渡す構成にした
- スキルは progressive disclosure（エージェントが必要時に skills ツールで読み込む）で動作するため、
  構造化出力を依頼するプロンプトで明示的にスキルの参照を指示している
