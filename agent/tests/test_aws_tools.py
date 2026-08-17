"""CloudWatch 調査ツール群のテスト。

boto3 クライアントはフェイクを注入してテストする。
"""

import json
from typing import Any

from ops_agent.aws_tools import (
    build_tools,
    describe_alarms,
    get_alarm_history,
    get_metric_statistics,
    list_log_groups,
    query_logs,
)
from ops_agent.config import Config

CONFIG = Config.from_env(
    {
        "SNS_TOPIC_ARN": "arn:aws:sns:ap-northeast-1:123456789012:topic",
        "TARGET_REGIONS": "ap-northeast-1,us-east-1",
        "AWS_REGION": "ap-northeast-1",
    }
)


class FakeClientFactory:
    """boto3.client の代わりに使うフェイク。作成されたクライアントを記録する。"""

    def __init__(self, clients: dict[str, Any]) -> None:
        self.clients = clients
        self.calls: list[tuple[str, str]] = []

    def __call__(self, service: str, region_name: str) -> Any:
        self.calls.append((service, region_name))
        return self.clients[service]


class FakeCloudWatch:
    def __init__(self) -> None:
        self.describe_alarms_kwargs: dict[str, Any] = {}
        self.alarm_history_kwargs: dict[str, Any] = {}
        self.metric_kwargs: dict[str, Any] = {}

    def describe_alarms(self, **kwargs: Any) -> dict[str, Any]:
        self.describe_alarms_kwargs = kwargs
        return {
            "MetricAlarms": [
                {
                    "AlarmName": "high-error-rate",
                    "StateValue": "ALARM",
                    "StateReason": "Threshold Crossed",
                    "MetricName": "Errors",
                    "Namespace": "AWS/Lambda",
                }
            ],
            "CompositeAlarms": [],
        }

    def describe_alarm_history(self, **kwargs: Any) -> dict[str, Any]:
        self.alarm_history_kwargs = kwargs
        return {
            "AlarmHistoryItems": [
                {
                    "AlarmName": "high-error-rate",
                    "HistoryItemType": "StateUpdate",
                    "HistorySummary": "Alarm updated from OK to ALARM",
                }
            ]
        }

    def get_metric_statistics(self, **kwargs: Any) -> dict[str, Any]:
        self.metric_kwargs = kwargs
        return {
            "Label": "Errors",
            "Datapoints": [{"Average": 5.0}],
        }


class FakeLogs:
    def __init__(self) -> None:
        self.start_query_kwargs: dict[str, Any] = {}

    def describe_log_groups(self, **kwargs: Any) -> dict[str, Any]:
        return {"logGroups": [{"logGroupName": "/aws/lambda/my-func", "storedBytes": 123}]}

    def start_query(self, **kwargs: Any) -> dict[str, Any]:
        self.start_query_kwargs = kwargs
        return {"queryId": "query-1"}

    def get_query_results(self, queryId: str) -> dict[str, Any]:
        assert queryId == "query-1"
        return {
            "status": "Complete",
            "results": [
                [
                    {"field": "@message", "value": "ERROR: something broke"},
                ]
            ],
        }


def test_許可外リージョンはエラーメッセージを返す() -> None:
    factory = FakeClientFactory({})

    result = json.loads(describe_alarms(CONFIG, "eu-west-1", client_factory=factory))

    assert "error" in result
    assert "eu-west-1" in result["error"]
    assert factory.calls == []


def test_describe_alarmsは指定リージョンのアラームをJSONで返す() -> None:
    cw = FakeCloudWatch()
    factory = FakeClientFactory({"cloudwatch": cw})

    result = json.loads(describe_alarms(CONFIG, "us-east-1", client_factory=factory))

    assert factory.calls == [("cloudwatch", "us-east-1")]
    assert result["alarms"][0]["AlarmName"] == "high-error-rate"


def test_get_alarm_historyは過去24時間分を照会する() -> None:
    cw = FakeCloudWatch()
    factory = FakeClientFactory({"cloudwatch": cw})

    result = json.loads(get_alarm_history(CONFIG, "ap-northeast-1", client_factory=factory))

    assert result["history"][0]["AlarmName"] == "high-error-rate"
    kwargs = cw.alarm_history_kwargs
    delta = kwargs["EndDate"] - kwargs["StartDate"]
    assert delta.total_seconds() == CONFIG.lookback_hours * 3600


def test_list_log_groupsはロググループ一覧を返す() -> None:
    factory = FakeClientFactory({"logs": FakeLogs()})

    result = json.loads(list_log_groups(CONFIG, "ap-northeast-1", client_factory=factory))

    assert result["logGroups"][0]["logGroupName"] == "/aws/lambda/my-func"


def test_query_logsはLogsInsightsの結果を返す() -> None:
    logs = FakeLogs()
    factory = FakeClientFactory({"logs": logs})

    result = json.loads(
        query_logs(
            CONFIG,
            "ap-northeast-1",
            log_group_names="/aws/lambda/my-func",
            query_string="fields @message | filter @message like /ERROR/",
            client_factory=factory,
        )
    )

    assert result["status"] == "Complete"
    assert "ERROR: something broke" in json.dumps(result["results"])
    assert logs.start_query_kwargs["logGroupNames"] == ["/aws/lambda/my-func"]


def test_get_metric_statisticsはディメンションJSONを解釈する() -> None:
    cw = FakeCloudWatch()
    factory = FakeClientFactory({"cloudwatch": cw})

    result = json.loads(
        get_metric_statistics(
            CONFIG,
            "ap-northeast-1",
            namespace="AWS/Lambda",
            metric_name="Errors",
            dimensions_json='[{"Name": "FunctionName", "Value": "my-func"}]',
            stat="Sum",
            period_minutes=60,
            client_factory=factory,
        )
    )

    assert result["Label"] == "Errors"
    assert cw.metric_kwargs["Dimensions"] == [{"Name": "FunctionName", "Value": "my-func"}]
    assert cw.metric_kwargs["Statistics"] == ["Sum"]
    assert cw.metric_kwargs["Period"] == 3600


def test_build_toolsは5つのツールを返す() -> None:
    tools = build_tools(CONFIG)

    names = {t.tool_name for t in tools}
    assert names == {
        "describe_alarms",
        "get_alarm_history",
        "list_log_groups",
        "query_logs",
        "get_metric_statistics",
    }
