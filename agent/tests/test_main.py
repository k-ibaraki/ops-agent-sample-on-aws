"""AgentCore Runtime エントリポイントのテスト。"""

from typing import Any

import ops_agent.main as main_module
from ops_agent.models import AdhocReport, DailyReport, Finding


def test_entrypointは日次チェックを実行して結果概要を返す(monkeypatch: Any) -> None:
    report = DailyReport(
        overall_summary="問題なし",
        findings=[
            Finding(
                title="軽微な問題",
                score=20,
                region="ap-northeast-1",
                resource="r",
                summary="s",
                recommendation="a",
            )
        ],
    )
    captured: dict[str, Any] = {}

    def fake_run_daily_check(config: Any) -> DailyReport:
        captured["config"] = config
        return report

    monkeypatch.setenv("SNS_TOPIC_ARN", "arn:aws:sns:ap-northeast-1:123456789012:topic")
    monkeypatch.setenv("AWS_REGION", "ap-northeast-1")
    monkeypatch.setattr(main_module, "run_daily_check", fake_run_daily_check)

    result = main_module.invoke({"trigger": "scheduled"})

    assert result["status"] == "ok"
    assert result["mode"] == "daily"
    assert result["findings_count"] == 1
    assert result["notable_count"] == 0
    assert captured["config"].sns_topic_arn.endswith(":topic")


def test_entrypointは調査依頼を受けたらアドホック調査に振り分ける(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_run_adhoc(config: Any, question: str) -> AdhocReport:
        captured["question"] = question
        return AdhocReport(answer="回答", findings=[])

    def fail_daily(config: Any) -> DailyReport:
        raise AssertionError("日次チェックが呼ばれてはいけない")

    monkeypatch.setenv("SNS_TOPIC_ARN", "arn:aws:sns:ap-northeast-1:123456789012:topic")
    monkeypatch.setenv("AWS_REGION", "ap-northeast-1")
    monkeypatch.setattr(main_module, "run_adhoc_investigation", fake_run_adhoc)
    monkeypatch.setattr(main_module, "run_daily_check", fail_daily)

    result = main_module.invoke({"trigger": "adhoc", "message": "昨日のエラーを詳しく"})

    assert result["status"] == "ok"
    assert result["mode"] == "adhoc"
    assert captured["question"] == "昨日のエラーを詳しく"


def test_entrypointは日次チェックの失敗を失敗として返す(monkeypatch: Any) -> None:
    # 失敗はエージェント側で通知済みのため、ここで例外にすると
    # 中継 Lambda のエラーアラームが二重に鳴る
    def fake_run_daily_check(config: Any) -> DailyReport | None:
        return None

    monkeypatch.setenv("SNS_TOPIC_ARN", "arn:aws:sns:ap-northeast-1:123456789012:topic")
    monkeypatch.setenv("AWS_REGION", "ap-northeast-1")
    monkeypatch.setattr(main_module, "run_daily_check", fake_run_daily_check)

    result = main_module.invoke({"trigger": "scheduled"})

    assert result == {"status": "failed", "mode": "daily"}


def test_entrypointはアドホック調査の失敗を失敗として返す(monkeypatch: Any) -> None:
    def fake_run_adhoc(config: Any, question: str) -> AdhocReport | None:
        return None

    monkeypatch.setenv("SNS_TOPIC_ARN", "arn:aws:sns:ap-northeast-1:123456789012:topic")
    monkeypatch.setenv("AWS_REGION", "ap-northeast-1")
    monkeypatch.setattr(main_module, "run_adhoc_investigation", fake_run_adhoc)

    result = main_module.invoke({"trigger": "adhoc", "message": "調べて"})

    assert result == {"status": "failed", "mode": "adhoc"}
