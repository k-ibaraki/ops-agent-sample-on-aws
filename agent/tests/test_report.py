"""通知メッセージ整形（Amazon Q Developer in chat apps カスタム通知形式）のテスト。"""

import json
from datetime import UTC, datetime

from ops_agent.models import AdhocReport, DailyReport, Finding
from ops_agent.report import (
    MAX_DESCRIPTION_CHARS,
    build_adhoc_notification,
    build_failure_notification,
    build_notification,
)

GENERATED_AT = datetime(2026, 8, 17, 8, 0, tzinfo=UTC)


def make_finding(score: int, title: str) -> Finding:
    return Finding(
        title=title,
        score=score,
        region="ap-northeast-1",
        resource="my-api-function",
        summary="エラー率が上昇している",
        recommendation="直近のデプロイをロールバックする",
    )


def test_カスタム通知形式のスキーマに従う() -> None:
    report = DailyReport(overall_summary="全体的に安定", findings=[])

    notification = build_notification(report, threshold=50, generated_at=GENERATED_AT)
    message = json.loads(notification.message)

    assert message["version"] == "1.0"
    assert message["source"] == "custom"
    assert message["content"]["textType"] == "client-markdown"
    assert isinstance(message["content"]["title"], str)
    assert isinstance(message["content"]["description"], str)


def test_問題なしの日はその旨のサマリになる() -> None:
    report = DailyReport(overall_summary="全リージョンで異常なし", findings=[])

    notification = build_notification(report, threshold=50, generated_at=GENERATED_AT)
    message = json.loads(notification.message)

    assert "異常なし" in message["content"]["title"]
    assert "全リージョンで異常なし" in message["content"]["description"]


def test_閾値以上の問題は詳細つきで強調される() -> None:
    report = DailyReport(
        overall_summary="1件の重要な問題を検出",
        findings=[make_finding(85, "Lambda エラー率急増"), make_finding(30, "軽微な問題")],
    )

    notification = build_notification(report, threshold=50, generated_at=GENERATED_AT)
    message = json.loads(notification.message)
    description = message["content"]["description"]

    assert "要注意 1件" in message["content"]["title"]
    assert "Lambda エラー率急増" in description
    assert "85" in description
    assert "my-api-function" in description
    assert "直近のデプロイをロールバックする" in description
    # 閾値未満の問題はタイトル一覧に載るが詳細は展開されない
    assert "軽微な問題" in description
    # 状況・推奨はラベル行 + 本文行に分かれ、段落として余白が入る
    assert "\n\n📊 状況:\n" in description
    assert "\n\n💡 推奨:\n" in description


def test_複数の問題の間には区切り線が入る() -> None:
    report = DailyReport(
        overall_summary="2件検出",
        findings=[make_finding(85, "問題A"), make_finding(60, "問題B")],
    )

    notification = build_notification(report, threshold=50, generated_at=GENERATED_AT)
    description = json.loads(notification.message)["content"]["description"]

    # 問題数 - 1 本の区切り線が入り、セクション見出しに件数が付く
    assert description.count("─" * 24) == 1
    assert "スコア 50 点以上の問題（2件）" in description


def test_問題が1件なら区切り線は入らない() -> None:
    report = DailyReport(
        overall_summary="1件検出",
        findings=[make_finding(85, "問題A")],
    )

    notification = build_notification(report, threshold=50, generated_at=GENERATED_AT)
    description = json.loads(notification.message)["content"]["description"]

    assert "─" * 24 not in description


def test_スコアに応じた深刻度の絵文字が付く() -> None:
    report = DailyReport(
        overall_summary="概況",
        findings=[
            make_finding(95, "重大障害"),
            make_finding(85, "早急対応"),
            make_finding(55, "要注意"),
            make_finding(30, "軽微"),
            make_finding(10, "情報"),
        ],
    )

    notification = build_notification(report, threshold=50, generated_at=GENERATED_AT)
    description = json.loads(notification.message)["content"]["description"]

    assert "*🔴 重大障害*" in description
    assert "*🟠 早急対応*" in description
    assert "*🟡 要注意*" in description
    # 閾値未満は一覧行に絵文字付きで並ぶ
    assert "🔵 軽微（スコア 30 / ap-northeast-1）" in description
    assert "⚪ 情報（スコア 10 / ap-northeast-1）" in description
    # セクション見出しにも絵文字が付く
    assert "*⚠️ スコア 50 点以上の問題（3件）*" in description
    assert "*📋 その他の検出事項（50 点未満）*" in description


def test_太字は行全体を包む形式でSlackでも崩れない() -> None:
    # Slack の mrkdwn は *...* の外側に全角文字が隣接すると太字にならないため、
    # 太字を使う行は「行全体が *...* で包まれている」ことを保証する
    report = DailyReport(
        overall_summary="1件の重要な問題を検出",
        findings=[make_finding(85, "Lambda エラー率急増"), make_finding(30, "軽微な問題")],
    )

    notification = build_notification(report, threshold=50, generated_at=GENERATED_AT)
    description = json.loads(notification.message)["content"]["description"]

    for line in description.split("\n"):
        if "*" in line:
            assert line.startswith("*") and line.endswith("*"), line
            assert "*" not in line[1:-1], line


def test_日付はJSTで解釈される() -> None:
    # UTC の 8/16 23:30 は JST では 8/17
    generated_at = datetime(2026, 8, 16, 23, 30, tzinfo=UTC)
    report = DailyReport(overall_summary="概況", findings=[])

    notification = build_notification(report, threshold=50, generated_at=generated_at)

    assert "2026-08-17" in notification.subject
    assert "2026-08-17" in json.loads(notification.message)["content"]["title"]


def test_件名は100文字以内で日付を含む() -> None:
    report = DailyReport(overall_summary="x" * 500, findings=[])

    notification = build_notification(report, threshold=50, generated_at=GENERATED_AT)

    assert len(notification.subject) <= 100
    assert "2026-08-17" in notification.subject


def test_日次通知の末尾に追加調査の依頼例が載る() -> None:
    report = DailyReport(overall_summary="異常なし", findings=[])

    notification = build_notification(report, threshold=50, generated_at=GENERATED_AT)
    description = json.loads(notification.message)["content"]["description"]

    assert description.rstrip().endswith('`@Amazon Q run ops "調べたい内容"`')


def test_依頼例は本文が長くても切り詰められない() -> None:
    # 本文を先に切り詰めてから依頼例を付けるため、依頼例は必ず残る
    report = DailyReport(overall_summary="x" * (MAX_DESCRIPTION_CHARS * 2), findings=[])

    notification = build_notification(report, threshold=50, generated_at=GENERATED_AT)
    description = json.loads(notification.message)["content"]["description"]

    assert "@Amazon Q run ops" in description
    assert len(description) <= MAX_DESCRIPTION_CHARS


def test_関数名を渡すと初回のエイリアス作成コマンドも載る() -> None:
    # 依頼方法だけ載せても、エイリアス未作成の人は実行できない
    report = DailyReport(overall_summary="異常なし", findings=[])

    notification = build_notification(
        report,
        threshold=50,
        generated_at=GENERATED_AT,
        invoker_function_name="OpsAgentOnAwsStack-invoker",
        invoker_region="ap-northeast-1",
    )
    description = json.loads(notification.message)["content"]["description"]

    assert "alias create ops" in description
    # 関数名は実物を載せる（利用者に調べさせない）
    assert "--function-name OpsAgentOnAwsStack-invoker" in description
    assert "--invocation-type Event" in description
    assert '"trigger": "adhoc"' in description
    # リージョン未指定だと Amazon Q Developer が実行時に入力を求めてくる
    assert "--region ap-northeast-1" in description
    # JSON は空白を含むため、--payload は必ず最後に置く
    assert description.index("--region") < description.index("--payload")


def test_リージョンが不明ならエイリアス作成コマンドは載せない() -> None:
    # 不完全な作成コマンドを載せると、実行時に入力を求められて詰まる
    report = DailyReport(overall_summary="異常なし", findings=[])

    notification = build_notification(
        report,
        threshold=50,
        generated_at=GENERATED_AT,
        invoker_function_name="OpsAgentOnAwsStack-invoker",
    )
    description = json.loads(notification.message)["content"]["description"]

    assert "alias create" not in description
    assert "@Amazon Q run ops" in description


def test_エイリアス作成コマンドも本文が長いとき切り詰められない() -> None:
    report = DailyReport(overall_summary="x" * (MAX_DESCRIPTION_CHARS * 2), findings=[])

    notification = build_notification(
        report,
        threshold=50,
        generated_at=GENERATED_AT,
        invoker_function_name="OpsAgentOnAwsStack-invoker",
        invoker_region="ap-northeast-1",
    )
    description = json.loads(notification.message)["content"]["description"]

    assert "alias create ops" in description
    assert len(description) <= MAX_DESCRIPTION_CHARS


def test_アドホック回答は依頼内容と回答を含む() -> None:
    report = AdhocReport(
        answer="my-api-function のエラーは依存先のタイムアウトが原因です",
        recommendations=["接続タイムアウトを見直す", "リトライ設定を確認する"],
        findings=[make_finding(70, "依存先タイムアウト")],
    )

    notification = build_adhoc_notification(
        report, question="昨日の Lambda エラーを詳しく", generated_at=GENERATED_AT
    )
    message = json.loads(notification.message)
    description = message["content"]["description"]

    assert message["source"] == "custom"
    # 依頼内容はエージェントの出力ではなく受け取った文字列をそのまま載せる
    assert "昨日の Lambda エラーを詳しく" in description
    assert "my-api-function のエラーは依存先のタイムアウトが原因です" in description
    # 見出しが無いと依頼内容と回答が地続きに見えるため、回答にも見出しを付ける
    assert "*📝 回答*\nmy-api-function のエラーは依存先のタイムアウトが原因です" in description
    assert description.index("*❓ 依頼内容*") < description.index("*📝 回答*")
    # 推奨アクションはコード側で採番する
    assert "1. 接続タイムアウトを見直す" in description
    assert "2. リトライ設定を確認する" in description
    assert "🟠 依存先タイムアウト（スコア 70 / ap-northeast-1）" in description


def test_アドホック回答の依頼内容は長さと改行が整えられる() -> None:
    report = AdhocReport(answer="回答")
    question = "とても長い依頼\n" * 200

    notification = build_adhoc_notification(report, question=question, generated_at=GENERATED_AT)
    description = json.loads(notification.message)["content"]["description"]

    assert len(description) <= MAX_DESCRIPTION_CHARS
    # 件名に改行は入れられない
    assert "\n" not in notification.subject
    assert len(notification.subject) <= 100


def test_アドホック回答の太字も行全体を包む() -> None:
    report = AdhocReport(
        answer="回答本文",
        recommendations=["対応1"],
        findings=[make_finding(80, "問題A")],
    )

    notification = build_adhoc_notification(report, question="質問", generated_at=GENERATED_AT)
    description = json.loads(notification.message)["content"]["description"]

    for line in description.split("\n"):
        if "*" in line:
            assert line.startswith("*") and line.endswith("*"), line
            assert "*" not in line[1:-1], line


def test_調査失敗時は依頼者に失敗を知らせる通知になる() -> None:
    notification = build_failure_notification(
        question="昨日の Lambda エラーを詳しく",
        error="ThrottlingException: rate exceeded",
        generated_at=GENERATED_AT,
    )
    message = json.loads(notification.message)

    description = message["content"]["description"]
    assert "失敗" in message["content"]["title"]
    assert "昨日の Lambda エラーを詳しく" in description
    assert "ThrottlingException" in description
    # 回答と同じく、依頼内容と地続きに見えないよう見出しで区切る
    assert "*📝 結果*" in description
