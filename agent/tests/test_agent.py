"""日次チェック・アドホック調査のオーケストレーションのテスト。

LLM 呼び出し（strands の Agent）はフェイクに差し替える。
"""

import json
import logging
from typing import Any

import pytest

import ops_agent.agent as agent_module
from ops_agent.agent import run_adhoc_investigation, run_daily_check
from ops_agent.config import Config
from ops_agent.models import AdhocReport, DailyReport, Finding

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


ADHOC_REPORT = AdhocReport(
    answer="エラーは依存先のタイムアウトが原因です",
    recommendations=["接続タイムアウトを見直す"],
)


class FakeAgentResult:
    """strands の AgentResult のうち、オーケストレーションが使う部分だけを模す。"""

    def __init__(self, structured_output: Any = None) -> None:
        self.structured_output = structured_output


class FakeAgent:
    def __init__(self, structured_result: Any = REPORT) -> None:
        self.prompts: list[str] = []
        self.structured_output_calls: list[Any] = []
        self.structured_result = structured_result
        # 本物の Agent は呼び出しのたびに会話履歴へ追記し、失敗しても巻き戻さない
        self.messages: list[Any] = []

    def __call__(
        self, prompt: str, *, structured_output_model: type | None = None
    ) -> FakeAgentResult:
        self.prompts.append(prompt)
        self.messages.append({"role": "user", "prompt": prompt})
        if structured_output_model is None:
            return FakeAgentResult()
        self.structured_output_calls.append((structured_output_model, prompt))
        return FakeAgentResult(self.structured_result)


class FakeSns:
    def __init__(self) -> None:
        self.publishes: list[dict[str, Any]] = []

    def publish(self, **kwargs: Any) -> dict[str, Any]:
        self.publishes.append(kwargs)
        return {"MessageId": "msg-1"}

    @property
    def publish_kwargs(self) -> dict[str, Any]:
        return self.publishes[-1]


@pytest.fixture
def waits(monkeypatch: Any) -> list[float]:
    """リトライの待機を実際には眠らずに記録する。"""
    recorded: list[float] = []
    monkeypatch.setattr(agent_module, "_sleep", recorded.append)
    return recorded


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


def test_日次通知に中継Lambdaの実名が載る() -> None:
    config = Config.from_env(
        {
            "SNS_TOPIC_ARN": CONFIG.sns_topic_arn,
            "AWS_REGION": "ap-northeast-1",
            "INVOKER_FUNCTION_NAME": "OpsAgentOnAwsStack-invoker",
            "INVOKER_REGION": "ap-northeast-1",
        }
    )
    sns = FakeSns()

    run_daily_check(config, agent=FakeAgent(), sns_client=sns)

    description = json.loads(sns.publish_kwargs["Message"])["content"]["description"]
    assert "--function-name OpsAgentOnAwsStack-invoker" in description
    assert "--region ap-northeast-1" in description


class FlakyStructuredOutputAgent(FakeAgent):
    """指定回数だけ構造化出力に失敗するエージェント。"""

    def __init__(self, failures: int, structured_result: Any = REPORT) -> None:
        super().__init__(structured_result)
        self.failures = failures

    def __call__(
        self, prompt: str, *, structured_output_model: type | None = None
    ) -> FakeAgentResult:
        result = super().__call__(prompt, structured_output_model=structured_output_model)
        calls = len(self.structured_output_calls)
        if structured_output_model is not None and calls <= self.failures:
            raise ValueError(
                "No valid tool use or tool use input was found in the Bedrock response."
            )
        return result


def test_構造化出力は間隔を空けて再試行して成功する(waits: list[float]) -> None:
    # モデルがツール呼び出しを返さないことが実際に起きたため、間隔を空けて数回まで粘る
    agent = FlakyStructuredOutputAgent(failures=2)

    report = run_daily_check(CONFIG, agent=agent, sns_client=FakeSns())

    assert report == REPORT
    assert len(agent.structured_output_calls) == 3
    assert waits == [4, 16]
    # 失敗した 2 回分の痕跡は残さない（調査 1 件 + 成功した構造化出力 1 件）
    assert len(agent.messages) == 2


def test_構造化出力が空でも再試行の対象になる(waits: list[float]) -> None:
    class EmptyThenFilledAgent(FakeAgent):
        def __call__(
            self, prompt: str, *, structured_output_model: type | None = None
        ) -> FakeAgentResult:
            result = super().__call__(prompt, structured_output_model=structured_output_model)
            if structured_output_model is not None and len(self.structured_output_calls) == 1:
                # 例外にはならないが構造化出力が返らない、という失敗の仕方もある
                return FakeAgentResult(None)
            return result

    agent = EmptyThenFilledAgent()

    report = run_daily_check(CONFIG, agent=agent, sns_client=FakeSns())

    assert report == REPORT
    assert waits == [4]


def test_日次チェックが失敗したら失敗通知を出して正常終了する(
    waits: list[float], caplog: pytest.LogCaptureFixture
) -> None:
    agent = FlakyStructuredOutputAgent(failures=99)
    sns = FakeSns()

    with caplog.at_level(logging.ERROR, logger="ops_agent.agent"):
        report = run_daily_check(CONFIG, agent=agent, sns_client=sns)

    # 通知できた失敗で例外を送出すると、中継 Lambda のエラーアラームが二重に鳴る
    assert report is None
    # 初回 + リトライ 2 回で打ち切る
    assert len(agent.structured_output_calls) == 3
    message = json.loads(sns.publish_kwargs["Message"])
    assert message["content"]["title"].startswith("❌ 日次ヘルスチェック")
    assert message["content"]["title"].endswith("実行に失敗")
    assert "No valid tool use" in message["content"]["description"]
    # Slack には出るが、実行ログにも失敗の記録を残す
    assert caplog.records


def test_日次の失敗通知自体に失敗したら例外を送出する(waits: list[float]) -> None:
    class BrokenSns(FakeSns):
        def publish(self, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("AuthorizationError: not authorized to perform sns:Publish")

    # 依頼者にも運用者にも何も届かないため、こちらはアラームで拾わせる
    agent = FlakyStructuredOutputAgent(failures=99)
    with pytest.raises(RuntimeError, match="sns:Publish"):
        run_daily_check(CONFIG, agent=agent, sns_client=BrokenSns())


def test_アドホック調査の構造化出力も再試行される(waits: list[float]) -> None:
    agent = FlakyStructuredOutputAgent(failures=1, structured_result=ADHOC_REPORT)
    sns = FakeSns()

    report = run_adhoc_investigation(CONFIG, "調べて", agent=agent, sns_client=sns)

    assert report == ADHOC_REPORT
    assert len(agent.structured_output_calls) == 2
    assert waits == [4]
    # 再試行で成功したので失敗通知は出ない
    assert "失敗" not in json.loads(sns.publish_kwargs["Message"])["content"]["title"]


def test_run_adhoc_investigationは調査から通知までを実行する() -> None:
    agent = FakeAgent(ADHOC_REPORT)
    sns = FakeSns()

    report = run_adhoc_investigation(
        CONFIG, "昨日の Lambda エラーを詳しく", agent=agent, sns_client=sns
    )

    assert report == ADHOC_REPORT
    assert "昨日の Lambda エラーを詳しく" in agent.prompts[0]
    # 構造化出力で AdhocReport を要求し、専用の書式スキルを参照させる
    assert agent.structured_output_calls[0][0] is AdhocReport
    assert "adhoc-report-style" in agent.structured_output_calls[0][1]
    message = json.loads(sns.publish_kwargs["Message"])
    assert "エラーは依存先のタイムアウトが原因です" in message["content"]["description"]


def test_アドホック調査の失敗は依頼者に通知して正常終了する(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FailingAgent(FakeAgent):
        def __call__(
            self, prompt: str, *, structured_output_model: type | None = None
        ) -> FakeAgentResult:
            raise RuntimeError("ThrottlingException: rate exceeded")

    sns = FakeSns()

    # 依頼者は結果を待っているため、失敗も必ず通知する
    with caplog.at_level(logging.ERROR, logger="ops_agent.agent"):
        report = run_adhoc_investigation(CONFIG, "調べて", agent=FailingAgent(), sns_client=sns)

    assert report is None
    message = json.loads(sns.publish_kwargs["Message"])
    assert "失敗" in message["content"]["title"]
    assert "ThrottlingException" in message["content"]["description"]
    assert caplog.records


def test_依頼内容が空なら調査せずに失敗を通知する() -> None:
    agent = FakeAgent(ADHOC_REPORT)
    sns = FakeSns()

    # 中継 Lambda は SNS 権限を持たないため、空依頼の通知はエージェント側で行う
    report = run_adhoc_investigation(CONFIG, "   ", agent=agent, sns_client=sns)

    assert report is None
    assert agent.prompts == []
    message = json.loads(sns.publish_kwargs["Message"])
    assert "失敗" in message["content"]["title"]


def test_エージェントの組み立てに失敗した場合も依頼者に通知される(monkeypatch: Any) -> None:
    def failing_build_agent(config: Config) -> Any:
        raise RuntimeError("ValidationException: model not found")

    monkeypatch.setattr(agent_module, "build_agent", failing_build_agent)
    sns = FakeSns()

    # モデル ID の誤りやスキルの不備は調査開始前に落ちるため、この経路も通知が要る
    report = run_adhoc_investigation(CONFIG, "調べて", sns_client=sns)

    assert report is None
    assert "失敗" in json.loads(sns.publish_kwargs["Message"])["content"]["title"]
