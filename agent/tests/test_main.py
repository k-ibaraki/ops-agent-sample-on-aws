"""AgentCore Runtime エントリポイントのテスト。"""

from typing import Any

import ops_agent.main as main_module
from ops_agent.models import DailyReport, Finding


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
    assert result["findings_count"] == 1
    assert result["notable_count"] == 0
    assert captured["config"].sns_topic_arn.endswith(":topic")
