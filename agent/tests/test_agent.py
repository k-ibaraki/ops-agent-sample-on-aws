"""日次チェックのオーケストレーション（run_daily_check）のテスト。

LLM 呼び出し（strands の Agent）はフェイクに差し替える。
"""

import json
from typing import Any

from ops_agent.agent import SKILLS_DIR, build_system_prompt, run_daily_check
from ops_agent.config import Config
from ops_agent.models import DailyReport, Finding

CONFIG = Config.from_env(
    {
        "SNS_TOPIC_ARN": "arn:aws:sns:ap-northeast-1:123456789012:topic",
        "TARGET_REGIONS": "ap-northeast-1,us-east-1",
        "AWS_REGION": "ap-northeast-1",
        "SCORE_THRESHOLD": "50",
    }
)

REPORT = DailyReport(
    overall_summary="Lambda のエラー率上昇を検出",
    findings=[
        Finding(
            title="Lambda エラー率急増",
            score=85,
            region="ap-northeast-1",
            resource="my-api-function",
            summary="エラー率が上昇している",
            recommendation="直近のデプロイをロールバックする",
        )
    ],
)


class FakeAgent:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.structured_output_calls: list[Any] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return "調査完了"

    def structured_output(self, output_model: type, prompt: str) -> DailyReport:
        self.structured_output_calls.append((output_model, prompt))
        return REPORT


class FakeSns:
    def __init__(self) -> None:
        self.publish_kwargs: dict[str, Any] = {}

    def publish(self, **kwargs: Any) -> dict[str, Any]:
        self.publish_kwargs = kwargs
        return {"MessageId": "msg-1"}


def test_システムプロンプトに採点基準が含まれる() -> None:
    prompt = build_system_prompt(CONFIG)

    assert "100" in prompt
    assert "影響範囲" in prompt
    assert "緊急度" in prompt
    assert "継続性" in prompt


def test_書式スキルの定義が存在する() -> None:
    skill_md = SKILLS_DIR / "slack-report-style" / "SKILL.md"

    assert skill_md.is_file()
    content = skill_md.read_text(encoding="utf-8")
    assert "name: slack-report-style" in content
    assert "description:" in content


def test_run_daily_checkは調査から通知までを実行する() -> None:
    agent = FakeAgent()
    sns = FakeSns()

    report = run_daily_check(CONFIG, agent=agent, sns_client=sns)

    assert report == REPORT
    # 調査プロンプトに対象リージョンと期間が含まれる
    assert "ap-northeast-1" in agent.prompts[0]
    assert "us-east-1" in agent.prompts[0]
    assert "24" in agent.prompts[0]
    # 構造化出力で DailyReport を要求し、書式スキルの参照を指示している
    assert agent.structured_output_calls[0][0] is DailyReport
    assert "slack-report-style" in agent.structured_output_calls[0][1]
    # 通知が1回発行され、検出内容が含まれる
    assert sns.publish_kwargs["TopicArn"] == CONFIG.sns_topic_arn
    message = json.loads(sns.publish_kwargs["Message"])
    assert "Lambda エラー率急増" in message["content"]["description"]
