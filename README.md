# ops-agent-sample-on-aws

AWS アカウントの日次ヘルスチェックを行う運用エージェントのサンプルです。

毎朝定時に [Amazon Bedrock AgentCore](https://aws.amazon.com/bedrock/agentcore/) 上のエージェント（[Strands Agents](https://strandsagents.com/) / Python）が起動し、過去 24 時間の CloudWatch（アラーム・ログ・メトリクス）を自律的に調査します。見つけた問題に 0〜100 点のスコアを付け、結果を SNS 経由で Slack に通知します。

## アーキテクチャ

![AWS 構成図](docs/architecture.drawio.png)

- エージェントには CloudWatch の調査ツール（boto3 の薄いラッパー、読み取り専用）だけを渡し、どこをどう深掘りするかはエージェント自身が判断します
- 採点結果は Pydantic モデルの構造化出力で受け取り、通知メッセージの整形と SNS 発行は LLM を介さない通常のコードで行います
- レポート文面の書式（改行・箇条書き・文体）は [Strands の Skills 機能](https://strandsagents.com/docs/user-guide/concepts/plugins/skills/) で定義しています（`agent/src/ops_agent/skills/slack-report-style/SKILL.md`）
- 問題ゼロの日も「異常なし」のサマリを毎日 1 通送るため、通知が来ないこととエージェントの停止を区別できます

設計の経緯と決定事項は [docs/DESIGN.md](docs/DESIGN.md) を参照してください。

## 前提条件

| ツール | 用途 |
|--------|------|
| [mise](https://mise.jdx.dev/) | Node.js / pnpm / uv のバージョン管理 |
| Docker | エージェントのコンテナイメージビルド（linux/arm64） |
| AWS CLI + 認証情報 | デプロイ先アカウントへのアクセス |

このほか、デプロイ先リージョン（デフォルト: 東京）の Bedrock で Claude モデル
（デフォルト: `jp.anthropic.claude-sonnet-4-6`）が利用可能になっている必要があります。

## セットアップ

```sh
mise install          # Node.js / pnpm / uv を導入
pnpm install          # CDK 側の依存関係

# デプロイパラメータを作成（parameter.ts は gitignore 済み）
cp parameter.sample.ts parameter.ts

cd agent && uv sync   # エージェント側の依存関係（開発時のみ）
```

## デプロイ

```sh
# 初回のみ（CDK を使ったことがないアカウント/リージョンの場合）
pnpm cdk bootstrap

pnpm cdk deploy
```

デプロイが完了すると、毎朝 8:00 JST にエージェントが起動します。

### Slack 通知の設定（任意）

Slack 連携は [Amazon Q Developer in chat applications](https://docs.aws.amazon.com/chatbot/latest/adminguide/what-is.html)（旧 AWS Chatbot）を使います。シークレット管理は不要です。

1. AWS コンソールの Amazon Q Developer in chat applications で、Slack ワークスペースを承認する（初回のみ手動）
2. ワークスペース ID（`T` で始まる）とチャンネル ID（`C` で始まる）を控える
3. `parameter.ts` に設定してデプロイする

```ts
// parameter.ts
export const parameters: OpsAgentParameters = {
  // ...
  slackWorkspaceId: "TXXXXXXXX",
  slackChannelId: "CXXXXXXXX",
};
```

ID を設定しない場合は SNS トピックまで作成されるので、メール購読などでも動作確認できます。

### 設定一覧（parameter.ts）

パラメータはリポジトリ直下の `parameter.ts`（[parameter.sample.ts](parameter.sample.ts) のコピー）で設定します。
`parameter.ts` は gitignore されているため、個人環境の値がコミットされることはありません。

| キー | デフォルト | 説明 |
|------|-----------|------|
| `scheduleCron` | `cron(0 8 * * ? *)` | 実行スケジュール（EventBridge Scheduler の cron 式） |
| `scheduleTimeZone` | `Asia/Tokyo` | スケジュールのタイムゾーン |
| `modelId` | `jp.anthropic.claude-sonnet-4-6` | エージェントが使う Bedrock モデル ID |
| `scoreThreshold` | `50` | 通知で強調するスコアの閾値 |
| `lookbackHours` | `24` | 調査対象の期間（時間） |
| `targetRegions` | `[]`（デプロイ先のみ） | 監視対象リージョンの配列 |
| `slackWorkspaceId` | なし | Slack ワークスペース ID |
| `slackChannelId` | なし | Slack チャンネル ID |

## 動作確認（手動実行）

スケジュールを待たずに試すには、中継 Lambda を直接呼び出します。

```sh
aws lambda invoke \
  --function-name <InvokerFunction の関数名> \
  --cli-binary-format raw-in-base64-out \
  --payload '{}' /dev/stdout
```

## 開発

テスト駆動開発（TDD）で実装しています。Python 側は ruff（lint / フォーマット）と ty（型チェック）、CDK 側は tsc + jest を使います。

```sh
# Python（agent/ ディレクトリで実行）
uv run pytest             # テスト
uv run ruff format .      # フォーマット
uv run ruff check .       # lint
uv run ty check           # 型チェック

# CDK（リポジトリルートで実行）
pnpm build                # 型チェック
pnpm test                 # jest（Template アサーション）
pnpm cdk synth --quiet    # synth 確認
```

## セキュリティに関する注意

- エージェントはログ本文という信頼できない入力を読むため、理論上はログに仕込まれた指示文によるプロンプトインジェクションがあり得ます。このサンプルでは、エージェントに読み取り専用ツールだけを渡し、通知の整形・送信を LLM を介さないコードで行うことで、実害につながる経路を塞いでいます
- ツールの追加などで拡張する場合も、書き込み系の権限をエージェントに与えない方針を維持することをおすすめします

## コストに関する注意

- 実行のたびに Bedrock のモデル呼び出し料金と Logs Insights のクエリ料金（スキャン量課金）が発生します
- エージェントが自律的にクエリを発行するため、ログが大量にあるアカウントではスキャン量が膨らむ可能性があります。まずはログの少ない検証用アカウントでの実行をおすすめします

## 拡張のアイデア

- Slack からの指示駆動: 受付（API Gateway + Lambda など）を追加すれば、「この手順を実行して」といった対話型の運用エージェントに発展させられます
- 調査結果の永続化: 詳細レポートを S3 に保存すれば、後からの振り返りや監査に使えます
- AgentCore Observability: トレースを有効化すると、エージェントの思考過程やツール呼び出しを CloudWatch で確認できます
