"""採点結果スキーマ（Finding / DailyReport / AdhocReport）のテスト。"""

import pytest
from pydantic import ValidationError

from ops_agent.models import AdhocReport, DailyReport, Finding


def make_finding(score: int, title: str = "テスト問題") -> Finding:
    return Finding(
        title=title,
        score=score,
        region="ap-northeast-1",
        resource="test-resource",
        summary="概要",
        recommendation="推奨アクション",
    )


def test_スコアは0から100の範囲に制限される() -> None:
    with pytest.raises(ValidationError):
        make_finding(101)
    with pytest.raises(ValidationError):
        make_finding(-1)


def test_notable_findingsは閾値以上をスコア降順で返す() -> None:
    report = DailyReport(
        overall_summary="全体サマリ",
        findings=[
            make_finding(30, "軽微"),
            make_finding(80, "重要"),
            make_finding(50, "ちょうど閾値"),
        ],
    )

    notable = report.notable_findings(threshold=50)

    assert [f.title for f in notable] == ["重要", "ちょうど閾値"]


def test_問題ゼロでもレポートは成立する() -> None:
    report = DailyReport(overall_summary="問題なし", findings=[])

    assert report.notable_findings(threshold=50) == []


def test_アドホック回答は本文だけでも成立する() -> None:
    # 質問への回答が主で、推奨アクションや関連する問題は任意
    report = AdhocReport(answer="直近24時間にエラーはありません")

    assert report.recommendations == []
    assert report.findings == []
