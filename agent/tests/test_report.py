"""通知メッセージ整形（Amazon Q Developer in chat apps カスタム通知形式）のテスト。"""

import json
from datetime import UTC, datetime

from ops_agent.models import DailyReport, Finding
from ops_agent.report import build_notification

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
