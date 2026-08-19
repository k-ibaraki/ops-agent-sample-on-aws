"""CloudWatch 調査ツール群のテスト。

boto3 クライアントはフェイクを注入してテストする。
"""

import json
from typing import Any

from ops_agent.aws_tools import (
    MAX_LOG_EVENTS,
    build_tools,
    describe_alarms,
    describe_log_streams,
    filter_log_events,
    get_alarm_history,
    get_metric_statistics,
    list_log_groups,
    list_metrics,
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
        self.list_metrics_kwargs: dict[str, Any] = {}

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

    def list_metrics(self, **kwargs: Any) -> dict[str, Any]:
        self.list_metrics_kwargs = kwargs
        return {
            "Metrics": [
                {
                    "Namespace": "AWS/Lambda",
                    "MetricName": "Errors",
                    "Dimensions": [{"Name": "FunctionName", "Value": "my-func"}],
                }
            ]
        }


class FakeLogs:
    def __init__(self) -> None:
        self.start_query_kwargs: dict[str, Any] = {}
        self.filter_kwargs: dict[str, Any] = {}
        self.log_streams_kwargs: dict[str, Any] = {}

    def describe_log_groups(self, **kwargs: Any) -> dict[str, Any]:
        return {"logGroups": [{"logGroupName": "/aws/lambda/my-func", "storedBytes": 123}]}

    def filter_log_events(self, **kwargs: Any) -> dict[str, Any]:
        self.filter_kwargs = kwargs
        return {
            "events": [
                {
                    # 2026-08-17T08:00:00Z のエポックミリ秒
                    "timestamp": 1786953600000,
                    "logStreamName": "2026/08/17/[$LATEST]abc",
                    "message": "ERROR: something broke",
                }
            ]
        }

    def describe_log_streams(self, **kwargs: Any) -> dict[str, Any]:
        self.log_streams_kwargs = kwargs
        return {
            "logStreams": [
                {
                    "logStreamName": "2026/08/17/[$LATEST]abc",
                    "creationTime": 1786867200000,
                    "firstEventTimestamp": 1786867200000,
                    "lastEventTimestamp": 1786953600000,
                    "arn": "arn:aws:logs:ap-northeast-1:123456789012:log-group:g:log-stream:s",
                }
            ]
        }

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
    # 時刻の解釈を自己記述する注記が含まれる
    assert "UTC" in result["timezone_note"]
    assert "アプリケーション依存" in result["timezone_note"]


def test_query_logsはタイムアウト時にクエリを停止してエラーを返す() -> None:
    class FakeLogsNeverDone(FakeLogs):
        def __init__(self) -> None:
            super().__init__()
            self.stopped: list[str] = []

        def get_query_results(self, queryId: str) -> dict[str, Any]:
            return {"status": "Running", "results": []}

        def stop_query(self, queryId: str) -> dict[str, Any]:
            self.stopped.append(queryId)
            return {"success": True}

    logs = FakeLogsNeverDone()
    factory = FakeClientFactory({"logs": logs})

    result = json.loads(
        query_logs(
            CONFIG,
            "ap-northeast-1",
            log_group_names="/aws/lambda/my-func",
            query_string="fields @message",
            client_factory=factory,
            timeout_seconds=0,
        )
    )

    assert "error" in result
    assert logs.stopped == ["query-1"]


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


def test_期間を指定すると調査対象の期間が広がる() -> None:
    cw = FakeCloudWatch()
    factory = FakeClientFactory({"cloudwatch": cw})

    get_alarm_history(CONFIG, "ap-northeast-1", hours=72, client_factory=factory)

    kwargs = cw.alarm_history_kwargs
    assert (kwargs["EndDate"] - kwargs["StartDate"]).total_seconds() == 72 * 3600


def test_期間は上限を超えられない() -> None:
    cw = FakeCloudWatch()
    factory = FakeClientFactory({"cloudwatch": cw})

    # スキャン量の暴走を防ぐため、上限（既定 168 時間）で頭打ちにする
    get_alarm_history(CONFIG, "ap-northeast-1", hours=10000, client_factory=factory)

    kwargs = cw.alarm_history_kwargs
    delta = kwargs["EndDate"] - kwargs["StartDate"]
    assert delta.total_seconds() == CONFIG.max_lookback_hours * 3600


def test_filter_log_eventsはログイベントをUTCのISO形式で返す() -> None:
    logs = FakeLogs()
    factory = FakeClientFactory({"logs": logs})

    result = json.loads(
        filter_log_events(
            CONFIG,
            "ap-northeast-1",
            log_group_name="/aws/lambda/my-func",
            filter_pattern="ERROR",
            client_factory=factory,
        )
    )

    assert result["events"][0]["message"] == "ERROR: something broke"
    assert result["events"][0]["timestamp"] == "2026-08-17T08:00:00+00:00"
    assert logs.filter_kwargs["logGroupName"] == "/aws/lambda/my-func"
    assert logs.filter_kwargs["filterPattern"] == "ERROR"
    # 取得件数に上限を設けてコンテキストの肥大を防ぐ
    assert logs.filter_kwargs["limit"] == MAX_LOG_EVENTS
    assert "UTC" in result["timezone_note"]


def test_filter_log_eventsは長いメッセージを切り詰める() -> None:
    class FakeLogsHuge(FakeLogs):
        def filter_log_events(self, **kwargs: Any) -> dict[str, Any]:
            self.filter_kwargs = kwargs
            return {"events": [{"timestamp": 1786953600000, "message": "x" * 10000}]}

    factory = FakeClientFactory({"logs": FakeLogsHuge()})

    result = json.loads(
        filter_log_events(
            CONFIG, "ap-northeast-1", log_group_name="/aws/lambda/my-func", client_factory=factory
        )
    )

    assert len(result["events"][0]["message"]) < 10000


def test_describe_log_streamsは最終書き込みの新しい順に返す() -> None:
    logs = FakeLogs()
    factory = FakeClientFactory({"logs": logs})

    result = json.loads(
        describe_log_streams(
            CONFIG, "ap-northeast-1", log_group_name="/aws/lambda/my-func", client_factory=factory
        )
    )

    stream = result["logStreams"][0]
    assert stream["logStreamName"] == "2026/08/17/[$LATEST]abc"
    assert logs.log_streams_kwargs["orderBy"] == "LastEventTime"
    assert logs.log_streams_kwargs["descending"] is True
    # エポックミリ秒と ISO 形式が混在すると読み手が誤解するため、時刻はすべて変換する
    assert stream["creationTime"] == "2026-08-16T08:00:00+00:00"
    assert stream["firstEventTimestamp"] == "2026-08-16T08:00:00+00:00"
    assert stream["lastEventTimestamp"] == "2026-08-17T08:00:00+00:00"


def test_list_metricsは名前空間で絞り込める() -> None:
    cw = FakeCloudWatch()
    factory = FakeClientFactory({"cloudwatch": cw})

    result = json.loads(
        list_metrics(CONFIG, "ap-northeast-1", namespace="AWS/Lambda", client_factory=factory)
    )

    assert result["metrics"][0]["MetricName"] == "Errors"
    assert cw.list_metrics_kwargs["Namespace"] == "AWS/Lambda"
    # 未指定の絞り込み条件は API に渡さない
    assert "MetricName" not in cw.list_metrics_kwargs


def test_build_toolsは8つのツールを返す() -> None:
    tools = build_tools(CONFIG)

    names = {t.tool_name for t in tools}
    assert names == {
        "describe_alarms",
        "get_alarm_history",
        "list_log_groups",
        "query_logs",
        "get_metric_statistics",
        "filter_log_events",
        "describe_log_streams",
        "list_metrics",
    }
