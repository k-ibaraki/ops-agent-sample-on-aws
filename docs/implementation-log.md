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
- 絵文字も同じ分担で導入: 深刻度（🔴🟠🟡🔵⚪ スコア帯と対応）・セクション見出し・ラベルは
  `report.py` が決定的に付与し、本文中の補強的な絵文字（📈 🔁 など)はスキルで
  「1 行 1 個まで・行頭」のルールを与えてエージェントに使わせる
- 時刻の扱いを「思考は UTC・報告は JST」で統一: ツール（AWS API）が返す時刻は UTC のままとし、
  レポート文中の時刻は JST（UTC+9）へ変換して「JST」を明記するルールを
  システムプロンプトとスキルの両方に定義。通知タイトルの日付は従来から `report.py` が JST で決定的に付与
- ただし「ツールの時刻はすべて UTC」という断定はレビューで撤回した。UTC を保証できるのは
  AWS API のタイムスタンプ（オフセット付き）と Logs Insights の @timestamp までで、
  ログ本文（@message）内の時刻はアプリケーション依存でタイムゾーン保証がない。
  プロンプトを 3 層（API=UTC / @timestamp=UTC / ログ本文=保証なし・不明なら変換せず引用）に
  書き直し、`query_logs` の応答 JSON にも `timezone_note` を追加してデータ境界で自己記述させた
- 複数の問題が並ぶと境目が分かりにくいという指摘を受け、問題同士の間に区切り線（─ x 24）を挟み、
  セクション見出しに件数を付けた

## 2026-08-19: Slack からの追加調査依頼に対応

日次通知を読んで気になった点を、その場で Slack から深掘りできるようにした（DESIGN.md 決定 18〜25）。
エージェントが実作業（書き込み系の操作）をする機能は見送り、調査と報告だけに留めている。

### 受け口の選定

当初は「シークレット管理が要るので新規 Slack App しかない」と判断したが、これは誤りだった。
Amazon Q Developer in chat applications は通知だけでなく双方向に対応しており、
チャンネルから `lambda invoke` を実行できる（公式チュートリアルもある）。
さらにコマンドエイリアスは `$変数` のプレースホルダを `--payload` の JSON 内に埋め込めるため、
シークレット管理を増やさずに自由文の依頼を受けられる。

- エイリアス定義: `@Amazon Q alias create ops lambda invoke --function-name <名前> --invocation-type Event --payload {"trigger": "adhoc", "message": "$question"}`
- 実行: `@Amazon Q run ops "..."`
- 同じエイリアスは位置引数（`run ops "..."`）と名前付き（`run ops --question "..."`）の
  どちらでも呼べる。パラメータが 1 つなので最短の位置引数を主な案内とし、
  名前付き形式は README に補足として載せるに留めた。
  なお名前付きのフラグ名は定義の変数名がそのまま使われる仕様で、`-q` のような
  ハイフン 1 つの短縮形は文書化されていない
- エイリアスはチャンネルごとの設定で CDK 管理外のため、README に手動手順として記載した

### 実装

- `Config.max_lookback_hours`（環境変数 `MAX_LOOKBACK_HOURS` / `parameter.ts` の `maxLookbackHours`、デフォルト 168 時間）を追加。
  全調査ツールに `hours` 引数を足し、`_resolve_hours()` を唯一の経路にして上限をコード側で強制する。
  上限が `lookback_hours` を下回る設定は `Config` 側で丸める
- 調査ツールを 3 種追加（`filter_log_events` / `describe_log_streams` / `list_metrics`）。
  `filter_log_events` は件数（50 件）とメッセージ長（1000 文字）の両方に上限を設けた。
  Logs Insights と違いクエリ課金がないため、生ログの前後関係を追う用途ではこちらを使わせる
- `AdhocReport`（Pydantic）と `build_adhoc_notification()` / `build_failure_notification()` を追加。
  通知に載る依頼文は LLM の出力ではなく受け取った文字列をそのまま使う
- 日次通知の末尾に依頼のコマンド例を付けた。本文を先に切り詰めてから連結し、
  切り詰めでコマンド例が消えないようにしている
- 書式は `adhoc-report-style` スキルを新設して誘導。`AgentSkills` にはスキル個別ではなく
  `skills/` ディレクトリごと渡す形に変えた（親ディレクトリを渡すと配下のスキルをすべて読み込む）
- 中継 Lambda はペイロードの `trigger` で振り分ける。依頼文が空でも日次チェックには
  落とさない（重複通知と余計な課金になるため）。空依頼の判定はエージェント側に置いた。
  中継 Lambda は SNS 発行権限を持たず、非同期起動で戻り値も捨てられるため、
  ここで中止しても依頼者には何も伝わらないため
- Slack 連携時のみ、チャネルロールとガードレールポリシーの双方を中継 Lambda の
  `lambda:InvokeFunction` に限定して CDK で作成する。ガードレールは未指定だと
  AdministratorAccess が既定になるため、明示が必須

### 実装上の判断・つまずき

- 非同期実行のため、失敗すると依頼者が結果を待ち続ける穴があった。
  `run_adhoc_investigation()` で例外を捕まえ、失敗通知を発行してから再送出する形にした。
  エージェントの組み立て（モデル ID の誤りなど）と空依頼も同じ扱いにしている
- デプロイ前のセルフレビューで、システムプロンプトが日次採点前提のまま
  「過去 24 時間の状態を調査し、問題を採点してください」と固定されていることに気づいた。
  アドホック側のプロンプトが「最大 168 時間まで広げられる」と言っても、
  優先されやすいシステムプロンプトと矛盾して期間拡張が働かない恐れがあったため、
  調査期間（既定と上限は設定値を埋め込む）と役割（採点／依頼への回答）を
  両モード共通の書き方に改めた
- `InvestigatorAgent` プロトコルの `structured_output` を型パラメータ付き
  （`def structured_output[T: BaseModel](...) -> T`）にして、2 種類のレポートを扱えるようにした

### 検証結果

- Python: pytest 53 件パス / ruff・ty クリーン
- CDK: jest 13 件パス / `cdk synth` 成功
- 未検証（デプロイ時に確認する）:
  1. Amazon Q Developer のエイリアスが `--invocation-type Event` を受け付けるか。
     受け付けない場合は、同期応答で受付メッセージを返す受付 Lambda を新設して、
     そこから既存の中継 Lambda を `InvocationType=Event` で起動する形に切り替える
  2. `--payload` に生の JSON をそのまま渡せるか。AWS CLI v2 では base64 と解釈されるため
     README の手動実行例には `--cli-binary-format raw-in-base64-out` を付けている
     （公式チュートリアルは生の JSON を渡しているので通る見込み）。
     通らない場合はペイロードのエンコード方法を変えて対応する

## 2026-08-19: デプロイ後の動作確認と修正

東京リージョンへデプロイして日次チェックの動作を確認し、3 点を修正した。

### 構造化出力の間欠的な失敗

1 回目の手動実行が 88.8 秒で失敗した。

```
ValueError: No valid tool use or tool use input was found in the Bedrock response.
```

調査フェーズは完走しており、`structured_output` でモデルが期待するツール呼び出しを
返さなかったという内容。切り分けの結果、決定的なコードバグではなく間欠的な事象だった。

| 実行 | 結果 |
|------|------|
| デプロイ前のスケジュール実行 | 成功（135.6 秒） |
| デプロイ後 1 回目 | 失敗（88.8 秒） |
| デプロイ後 2 回目 | 成功（129 秒） |
| ローカル再現（同一コード・同一設定） | 成功 |

IAM にも権限不足はなかった。日次パスには失敗通知もリトライも無く、失敗すると Slack に
何も出ないため、`structured_output` を初回 + リトライ 2 回まで再試行するようにした。
Strands の `structured_output` は `temp_messages`（`self.messages` のコピー）を使い
会話履歴を変更しないため、単純な再試行で安全に実装できる。
（この前提は 2026-08-22 の方式変更で成り立たなくなった。後述の
「日次チェックの失敗を検知できるようにした」の節を参照）

### 中継 Lambda の関数名を固定

README のエイリアス作成手順と手動実行例が `<InvokerFunction の関数名>` のプレースホルダで、
利用者に CDK の自動生成名を調べさせる作りになっていた。関数名を `<スタック名>-invoker` で
固定し、README・`CfnOutput`・日次通知の 3 箇所すべてに実名を載せるようにした。

エージェント側には環境変数 `INVOKER_FUNCTION_NAME` で名前を渡す。construct 参照
（`invoker.functionArn`）ではなく文字列で持つことで、Runtime → Lambda → Runtime の
循環参照を避けている。日次通知の末尾には、依頼コマンドに加えて初回のエイリアス作成
コマンドも実名入りで載せ、通知からコピーするだけで使い始められるようにした。

### Slack でエイリアスを実行したらリージョンを聞かれた

実際に Slack でエイリアスを作って実行したところ、Amazon Q Developer が
「Enter a value for `region`」と入力を求めてきた。CLI コマンドの実行にはリージョンが
必要で、エイリアス定義に `--region` を入れていなかったため。

`@Amazon Q alias list` で確認したところ、`--function-name` も `--invocation-type Event` も
定義どおり保存されていた（実行時の表示が途中で切れていただけだった）。不足はリージョンのみ。
CDK から `INVOKER_REGION`（`cdk.Aws.REGION`）を渡し、README と日次通知の案内に
`--region` を含めるようにした。関数名とリージョンのどちらかが欠ける場合は、
途中で入力を求められる不完全な案内を出さないよう、作成コマンド自体を省く。

`--payload` の JSON は空白を含むため、`--region` はその前に置く。

なお `@Amazon Q alias help` で、公式ドキュメントに記載のない
`alias get` / `alias delete` が存在することも確認できた。作り直しは
`@Amazon Q alias delete ops` を挟む。

### Slack 経由の実行が完走し、未検証項目が解消

エイリアスに `--region` を足したうえで Slack から実行したところ、依頼から回答まで完走した。
これにより未検証だった 2 件がいずれも解消した。

1. Amazon Q Developer は `--invocation-type Event` を受け付ける
2. `--payload` に生の JSON をそのまま渡せる（base64 エンコードは不要）

依頼文が「対策を考えて」だったのに対し、エージェントは
「設定変更・実装作業は調査エージェントの権限外のため、推奨アクションの提示にとどめます」
と自ら回答しており、調査と報告に閉じる制約も期待どおり働いていた。

### 依頼内容と回答の境目が分かりにくい

実物の通知を見て、依頼内容の直後に回答が空行 1 つで続くため同じ塊に見えることが分かった。
他のセクション（依頼内容・推奨アクション・関連して見つかった問題）には見出しがあるのに、
回答だけ見出しが無いことが原因だった。`*📝 回答*` の見出しを追加し、失敗通知の説明文にも
同様に `*📝 結果*` を付けて体裁を揃えた。

区切り線を挟む案も検討したが、日次通知では区切り線を「問題と問題の境目」に使っており、
意味が二重になるため見送った。

### 受付メッセージ（「少々お待ちください」）は見送った

依頼を出しても Chatbot の応答が `StatusCode: 202` だけで分かりにくいため、受付を伝える
メッセージを返せないか検討したが、見送った。判断の材料になった事実を残しておく。

**Chatbot の応答そのものは差し替えられない。** 制約は Chatbot ではなく
`--invocation-type Event`（非同期）にある。非同期だと Lambda はペイロードを返さないため、
Chatbot には 202 しか渡らない。同期にすれば戻り値が表示される。

**同期実行の描画を実機で確認した。** 中継 Lambda に固定文字列を返す一時的な分岐を足し、
`--invocation-type Event` を外したエイリアスで実行した結果は次のとおり。

- 戻り値は `Payload:` に続けて JSON ブロックで表示される。日本語は読めるが、
  波括弧とキー名が付いた `{ "message": "..." }` の形になり、素の文章にはならない
- コマンド応答は ephemeral で、実行した本人にしか見えない（`Send to main channel`
  ボタンが付く）。このボタンや ephemeral 表示を消す設定は
  `@Amazon Q set preferences` に無く、Chatbot の固定仕様
- 依頼元のスレッドには正しく紐づく

**実装するなら受付 Lambda の新設が必要だった。** 同期にすると実処理を別途非同期で
起動する必要がある。ガードレールが中継 Lambda 1 本しか許可していないため、受付用の
Lambda を新設してガードレールの向き先を変える構成になる（自己再帰は Lambda の
再帰呼び出し検知にかかるため避けたい）。決定 19 の「リソースを増やさず最小構成を保つ」を
覆すことになり、得られる改善が「202 だけ」→「JSON ブロック内の日本語一文・本人のみ可視」に
留まることから、見合わないと判断した。

### 検証で分かった Slack 連携の挙動

上記の検証と Slack での実行を通じて、通知設計に効く事実が 2 つ分かった。

- **全角引用符は Chatbot が自動変換する**。実行されたコマンドに
  `--chatbot-replace-curly-quotes enable` が自動で付与されており、Slack の
  スマートクォート変換で依頼文が壊れる心配は要らない
- **カスタム通知は `metadata.threadId` でスレッド化できる**。同じ `threadId` の通知が
  Slack でグループ化される。チャンネル側の `@Amazon Q set preferences` で
  「Display notification updates in threads」が有効である必要があるが、既定で有効だった。
  今回は使っていないが、通知が増えたときの整理手段として使える

### README の手動実行例が多重実行を招く

`aws lambda invoke` の例に `--cli-read-timeout` が無く、AWS CLI の既定 60 秒で
タイムアウトしてリトライが走る。初回デプロイ時に中継 Lambda の boto3 クライアントで
踏んだのと同じ罠を、README の手順側で再現していた。`--cli-read-timeout 0` を追加し、
省略してはいけない理由も明記した。

## 2026-08-22: 日次チェックの失敗を検知できるようにした

日次実行が失敗しても Slack に何も出ないまま終わっていた問題に対応した（DESIGN.md 決定 26・29・30）。

### 原因の切り分け

08-22 08:00 JST の失敗は、中継 Lambda のログでは `RuntimeClientError: Received error (500)
from runtime` としか分からなかったが、AgentCore Runtime 側のログにスタックトレースが残っていた。

```
main.py:33 invoke → agent.py:141 run_daily_check → _structured_output
ValueError: No valid tool use or tool use input was found in the Bedrock response.
```

つまり AgentCore や Bedrock の障害ではなく、エージェント内の Python 例外がそのまま
外へ抜けた結果の 500 だった。捕捉可能な位置にあるため、中継 Lambda に SNS 権限を
足さずにエージェント側で通知できると分かった。

分かったことを 3 つ記録しておく。

- 既存のリトライ 3 回は 23:01:35 / 23:01:38 / 23:01:41 と約 5 秒で使い切っていた。
  `_structured_output` に待機が無く、事実上「即座に 3 連発」だった
- エージェント自身の調査レポートは「同種のエラーが 08-19 16:13 にも発生」と報告したが、
  そちらは `調査依頼の本文が空です`（空依頼）で別物だった。エージェントの分析も鵜呑みにはできない
- SNS の `NumberOfMessagesPublished` は当該時間帯にデータポイントが無く、
  日次パスは失敗すると本当に無言で終わることが裏付けられた

### 失敗の原因は構造化出力のコンテキスト汚染

Strands の `Agent.structured_output()` は `temp_messages = self.messages + prompt` と、
調査中のツール呼び出し履歴を丸ごと含めたコンテキストに対し、`tool_choice={"any": {}}` で
出力用ツール（`DailyReport`）だけを提示する。エラーが送出される
`strands/models/bedrock.py` の該当行に到達するのは「toolUse は返ったが名前が一致しなかった」
場合だけなので、モデルが履歴中の調査ツール名を返したと考えるのが素直だった。

`Agent.structured_output()` は Strands 側で非推奨になっており、
`agent(prompt, structured_output_model=...)` が推奨されている。こちらは出力用ツールを
エージェントループ内のツールとして扱うため、モデルが調査ツール名を返してもループが続き、
最終的に出力ツールへ収束する。日次・アドホックの両方をこの方式に移行した。

移行にあたって、リトライの安全性の根拠が変わることに注意が要った。旧方式は
`temp_messages`（`self.messages` のコピー）を使うため履歴が汚れず、単純な再試行が
そのまま安全だった。新方式は `invoke_async` がモデル呼び出しの前に
`_append_messages()` で履歴へ追記し、例外経路（`except Exception` は
トレーススパンを閉じて再送出するだけ）に巻き戻しが無い。そのため失敗した試行の
依頼メッセージが残り、2 回目は user メッセージが連続する。Bedrock Converse は
ロールの交互を検証するので、本来の原因と無関係な `ValidationException` で
確定的に失敗しかねなかった。`_structured_output()` で試行前の履歴を控え、
再試行の前に巻き戻すようにして旧方式と同じ不変条件を取り戻した
（フェイクにも `messages` を持たせ、巻き戻しをテストで固定している）。

### 失敗の伝え方

失敗の扱いを 2 段構えにした。

1. エージェント内で捕捉できた失敗は Slack へ通知して正常終了させる
   （`run_daily_check` / `run_adhoc_investigation` は `None` を返す）
2. 通知すら発行できなかった失敗だけを例外として送出し、中継 Lambda の
   `Errors` アラームで拾う

通知済みの失敗まで例外にすると、失敗通知とアラームが Slack に 2 通並ぶ。
とくに空依頼のような利用者の入力ミスでもアラームが鳴るのは筋が悪い。
その代わり実行ログの ERROR 行が消えないよう、通知の前に `logger.exception()` を呼んでいる。

### 検証結果

- Python: pytest 84 件パス / ruff・ty クリーン
- CDK: jest 17 件パス / `cdk synth` 成功
- 未検証（デプロイ時に確認する）:
  1. 新方式の戻り値（`AgentResult.structured_output`）が実際の Bedrock 応答で
     期待どおり埋まるか。ユニットテストはフェイクでしか確かめられない
  2. 構造化出力の要求がエージェントループ内で走るようになったため、調査ツールが
     全て使える状態になっている。モデルがレポート作成前に追加調査を始める可能性があり、
     実行時間とターン数が伸びていないかを実行ログで確かめる

## 2026-08-17: CDK スタックを関数ベースの定義に変更

`cdk.Stack` のサブクラス + コンストラクタで定義する CDK の定番パターンをやめ、
`createOpsAgentStack(scope, id, props)` というファクトリ関数に変更した。

- CDK は construct ツリーの scope さえ渡せば動くため、継承は必須ではない。
  関数内で `new cdk.Stack()` を作り、各リソースの scope に渡すだけでよい
- 論理 ID は変わらないため CloudFormation テンプレートは同一（`cdk diff` で差分ゼロを確認済み）。
  再デプロイは不要
- フォルダ名は関数ベースでも「スタック定義の置き場」であることに変わりがないため `stacks/` を維持
