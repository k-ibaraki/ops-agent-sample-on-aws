# 設計決定録（Design Decisions）

本リポジトリは「AWS アカウントの日次ヘルスチェックを行う運用エージェント」の汎用サンプルです。
実装に先立って確定した設計上の決定を記録します。

## 全体像

毎朝定時に AgentCore Runtime 上のエージェント（Strands Agents / Python）が起動し、
過去 24 時間の CloudWatch（アラーム・ログ・メトリクス）を自律調査して、
見つけた問題に 0〜100 点のスコアを付け、結果を SNS 経由で Slack に通知します。

通知を受けた人は、その場で Slack から追加調査を依頼できます。依頼は同じ中継 Lambda を
経由して同じエージェントに届き、回答は同じ Slack チャンネルに返ります。

![AWS 構成図](architecture.drawio.png)

（構成図は draw.io の XML を埋め込んだ PNG です。draw.io で開くとそのまま編集できます。
実線が毎朝の定期実行、破線が Slack からの追加調査依頼の経路です）

```
EventBridge Scheduler (毎朝 8:00 JST) ─┐
                                       ├→ 中継 Lambda (Python)
Slack からの依頼 (@Amazon Q run ops) ──┘     → AgentCore Runtime (Strands Agents / Python)
                                                 ├─ CloudWatch 調査ツール群（読み取り専用）
                                                 └─ 採点・整形 → SNS Publish
                                                      → Amazon Q Developer in chat applications
                                                        (旧 AWS Chatbot) → Slack
```

## 決定事項

| # | 論点 | 決定 | 理由 |
|---|------|------|------|
| 1 | サンプルの型 | スケジュール起動の定期監視を軸に、Slack からの依頼で追加調査を行う指示駆動も持つ。ただしエージェントが行うのは調査と報告までで、実作業（書き込み系の操作）は行わない | 定期監視だけでは「気になった点をその場で掘る」運用に届かないため。書き込み権限を持たせないことで、インジェクションの実害経路は塞いだまま保つ |
| 2 | 調査対象 | アラーム履歴 + ログ（Logs Insights）+ メトリクスの 3 種を専用ツールとしてエージェントに渡し、自律調査させる | エージェントによる自律的な深掘りをサンプルの見どころにするため |
| 3 | ツールと IAM | boto3 の薄いラッパーを専用ツールとして実装し、IAM は読み取りに必要なアクションのみの最小権限 | public サンプルとして権限設計の手本を示すため |
| 4 | デプロイ | すべて CDK（TypeScript）で完結。`aws_bedrockagentcore` の L2 construct（`Runtime` + `AgentRuntimeArtifact.fromAsset`）で Docker イメージをビルドしてデプロイ | `cdk deploy` 一発で環境が揃うようにするため |
| 5 | 起動経路 | EventBridge Scheduler → 中継 Lambda → `InvokeAgentRuntime` | Scheduler のユニバーサルターゲットで直接呼ぶと同期実行のブロッキング問題があり、Lambda を挟む方がタイムアウト・エラー制御が明示的になるため |
| 6 | 実行時刻 | 毎朝 8:00 JST をデフォルトとし、cron 式は `parameter.ts` で変更可能 | 始業前に過去 24 時間の報告が届くようにするため |
| 7 | リージョン / モデル | 東京（ap-northeast-1）にデプロイ。モデルのデフォルトは `jp.anthropic.claude-sonnet-4-6`（国内クロスリージョン推論プロファイル）、環境変数で差し替え可能 | 国内読者向けサンプルとして自然な構成のため |
| 8 | 監視対象リージョン | 設定リスト式（`parameter.ts`）。デフォルトはデプロイ先リージョンのみ | 複数リージョンにワークロードを持つアカウントにも対応できる汎用性のため |
| 9 | 通知経路 | エージェント → SNS → Amazon Q Developer in chat applications（旧 AWS Chatbot）→ Slack。`SlackChannelConfiguration` まで CDK で作成（workspace/channel ID 未指定ならスキップして SNS まで） | Slack のシークレット管理を不要にし、疎結合に保つため |
| 10 | 通知ポリシー | 毎日必ずサマリを 1 通送信。閾値（デフォルト 50 点）以上の問題がある日はその詳細を強調 | 死活確認を兼ね、「問題がなかった」と「動いていない」を区別できるようにするため |
| 11 | 結果の保存 | S3 等への永続化はしない。通知のみ（詳細は実行ログに残る） | 構成を最小に保つため |
| 12 | Observability | AgentCore Observability はスコープ外 | 同上 |
| 13 | Python 管理 | uv（`pyproject.toml` + `uv.lock`、Dockerfile 内も uv）。Python は 3.14 で統一（venv / Docker / Lambda / CI）。Node 側は mise + pnpm | 再現性とビルド速度のため |
| 14 | 品質ツール | 型チェック: ty / lint・フォーマット: ruff / テスト: pytest（TDD）。CDK 側は tsc + jest | プロジェクト方針 |
| 15 | ドキュメント | 日本語で統一 | 想定読者が国内のため |
| 16 | CI | GitHub Actions 1 本（ruff / ty / pytest / tsc / jest / cdk synth） | public サンプルとしての最低限の品質保証 |
| 17 | パラメータ管理 | 型付きの `parameter.ts` に集約（CDK context は使わない）。`parameter.sample.ts` をコミットし、実際の `parameter.ts` は gitignore | 型安全で編集箇所が明確になり、個人環境の値の誤コミットも防げるため |
| 18 | Slack からの依頼の受け口 | Amazon Q Developer in chat applications のコマンドエイリアス `ops` から中継 Lambda を起動する（`@Amazon Q run ops "..."`。同じエイリアスは `--question "..."` の名前付き形式でも呼べる）。新規 Slack App は作らない | 決定 9 と同じく Slack のシークレット管理を不要に保ったまま、自由文の依頼を受けられるため。パラメータが 1 つなので、最短の位置引数を主な案内とする |
| 19 | 依頼の起動方式 | エイリアスに `--invocation-type Event` を含め、既存の中継 Lambda を非同期起動する。受け口専用の Lambda は増やさない | 調査は数分かかり同期では返せないため。リソースを増やさず最小構成を保てるため |
| 20 | 依頼への応答 | 非同期。結果は日次と同じ SNS トピック経由で同じ Slack チャンネルへ返す。専用の `AdhocReport`（Pydantic）で構造化出力を受け、整形は LLM を介さない通常のコード。調査が失敗した場合も失敗した旨を通知する | 通知経路を再利用でき追加インフラが不要なため。非同期のため、失敗を伝えないと依頼者が待ち続けるため |
| 21 | 調査期間 | 全調査ツールに期間引数（`hours`）を追加。省略時は `lookbackHours`、上限は `maxLookbackHours`（デフォルト 168 時間 = 7 日）をコード側で強制する | 「先週からの傾向」のような依頼に応えつつ、Logs Insights のスキャン費用の暴走を防ぐため |
| 22 | 調査ツールの追加 | `filter_log_events`・`describe_log_streams`・`list_metrics` の 3 種を追加（いずれも読み取り専用） | 生ログの前後関係とメトリクス名の確認ができず、深掘り依頼に答えきれないため |
| 23 | Slack からのコマンド権限 | チャネルロールとガードレールポリシーの双方を、中継 Lambda に対する `lambda:InvokeFunction` だけに限定し、CDK で作成する | ガードレールは未指定だと AdministratorAccess が既定になるため。チャンネル参加者を信頼するモデルであることを構成として明示するため |
| 24 | 会話の継続 | スコープ外。依頼のたびに独立したセッションで実行する | セッション管理と履歴保持が必要になり、サンプルの規模を大きく超えるため |
| 25 | 依頼の多重実行 | 仕組みでは制限せず、コストの注意書きで補う | Amazon Q Developer の実行確認ボタンが事実上のワンクッションになるため |

## スコアリング方式

- エージェントは調査終了後、問題ごとに 0〜100 点のスコアと根拠を構造化出力（Pydantic モデル）で返す
- 採点基準（影響範囲・緊急度・継続性）はシステムプロンプトに明記する
- 通知メッセージの整形（見出し・スコア・区切り）と SNS 発行は LLM ではなく通常のコードで行い、テスト可能にする
- 文面そのもの（改行・箇条書き・文体）は Strands の Skills 機能（`slack-report-style` スキル）で誘導する

## リポジトリ構成

```
.
├── ops-agent-sample-on-aws.ts   # CDK アプリのエントリポイント
├── parameter.sample.ts          # デプロイパラメータのサンプル（parameter.ts にコピーして使う）
├── stacks/                      # CDK スタック
├── test/                        # CDK のテスト（jest）
├── agent/                       # エージェント本体（Python / uv / Strands Agents）
│   ├── src/ops_agent/
│   ├── tests/
│   └── Dockerfile
├── invoker/                     # 中継 Lambda（Python）
└── docs/                        # 設計決定録・実装記録
```
