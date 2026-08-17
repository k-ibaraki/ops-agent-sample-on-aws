"""CloudWatch 調査ツール群。

boto3 の薄いラッパーを Strands のツールとしてエージェントに渡す。
すべて読み取り専用の API のみを使い、対象リージョンは Config の許可リストで検証する。
"""

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
from strands import tool

from ops_agent.config import Config

# boto3.client と同じ呼び出し規約 (service, region_name=...) のファクトリ
ClientFactory = Callable[..., Any]


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _region_error(config: Config, region: str) -> str | None:
    if region not in config.target_regions:
        return _dumps(
            {
                "error": f"リージョン {region} は監視対象外です。"
                f"対象: {', '.join(config.target_regions)}"
            }
        )
    return None


def _time_range(config: Config) -> tuple[datetime, datetime]:
    end = datetime.now(UTC)
    return end - timedelta(hours=config.lookback_hours), end


def describe_alarms(
    config: Config, region: str, *, client_factory: ClientFactory = boto3.client
) -> str:
    """現在のアラーム一覧（メトリクス・複合）を JSON 文字列で返す。"""
    if error := _region_error(config, region):
        return error
    client = client_factory("cloudwatch", region_name=region)
    response = client.describe_alarms(MaxRecords=100)
    alarms = response.get("MetricAlarms", []) + response.get("CompositeAlarms", [])
    return _dumps({"alarms": alarms})


def get_alarm_history(
    config: Config, region: str, *, client_factory: ClientFactory = boto3.client
) -> str:
    """過去 lookback_hours 時間のアラーム状態変化の履歴を JSON 文字列で返す。"""
    if error := _region_error(config, region):
        return error
    client = client_factory("cloudwatch", region_name=region)
    start, end = _time_range(config)
    response = client.describe_alarm_history(
        HistoryItemType="StateUpdate",
        StartDate=start,
        EndDate=end,
        MaxRecords=100,
    )
    return _dumps({"history": response.get("AlarmHistoryItems", [])})


def list_log_groups(
    config: Config,
    region: str,
    name_prefix: str = "",
    *,
    client_factory: ClientFactory = boto3.client,
) -> str:
    """ロググループの一覧を JSON 文字列で返す。"""
    if error := _region_error(config, region):
        return error
    client = client_factory("logs", region_name=region)
    kwargs: dict[str, Any] = {"limit": 50}
    if name_prefix:
        kwargs["logGroupNamePrefix"] = name_prefix
    response = client.describe_log_groups(**kwargs)
    return _dumps({"logGroups": response.get("logGroups", [])})


def query_logs(
    config: Config,
    region: str,
    log_group_names: str,
    query_string: str,
    *,
    client_factory: ClientFactory = boto3.client,
    timeout_seconds: float = 60.0,
    poll_interval_seconds: float = 1.0,
) -> str:
    """Logs Insights クエリを実行し、完了を待って結果を JSON 文字列で返す。"""
    if error := _region_error(config, region):
        return error
    client = client_factory("logs", region_name=region)
    start, end = _time_range(config)
    groups = [g.strip() for g in log_group_names.split(",") if g.strip()]
    query_id = client.start_query(
        logGroupNames=groups,
        startTime=int(start.timestamp()),
        endTime=int(end.timestamp()),
        queryString=query_string,
    )["queryId"]

    deadline = time.monotonic() + timeout_seconds
    while True:
        response = client.get_query_results(queryId=query_id)
        if response["status"] not in ("Scheduled", "Running"):
            return _dumps({"status": response["status"], "results": response.get("results", [])})
        if time.monotonic() >= deadline:
            client.stop_query(queryId=query_id)
            return _dumps({"error": f"クエリが {timeout_seconds} 秒以内に完了しませんでした"})
        time.sleep(poll_interval_seconds)


def get_metric_statistics(
    config: Config,
    region: str,
    namespace: str,
    metric_name: str,
    dimensions_json: str = "",
    stat: str = "Average",
    period_minutes: int = 60,
    *,
    client_factory: ClientFactory = boto3.client,
) -> str:
    """指定メトリクスの統計値（過去 lookback_hours 時間）を JSON 文字列で返す。"""
    if error := _region_error(config, region):
        return error
    client = client_factory("cloudwatch", region_name=region)
    start, end = _time_range(config)
    response = client.get_metric_statistics(
        Namespace=namespace,
        MetricName=metric_name,
        Dimensions=json.loads(dimensions_json) if dimensions_json else [],
        StartTime=start,
        EndTime=end,
        Period=period_minutes * 60,
        Statistics=[stat],
    )
    datapoints = sorted(response.get("Datapoints", []), key=lambda d: str(d.get("Timestamp", "")))
    return _dumps({"Label": response.get("Label", metric_name), "Datapoints": datapoints})


def build_tools(config: Config, client_factory: ClientFactory = boto3.client) -> list[Any]:
    """Config を束縛した Strands ツールの一覧を返す。"""

    @tool(name="describe_alarms")
    def describe_alarms_tool(region: str) -> str:
        """指定リージョンの CloudWatch アラーム一覧（現在の状態つき）を取得する。

        Args:
            region: 対象リージョン（例: ap-northeast-1）
        """
        return describe_alarms(config, region, client_factory=client_factory)

    @tool(name="get_alarm_history")
    def get_alarm_history_tool(region: str) -> str:
        """指定リージョンの過去24時間（設定値）のアラーム状態変化の履歴を取得する。

        Args:
            region: 対象リージョン（例: ap-northeast-1）
        """
        return get_alarm_history(config, region, client_factory=client_factory)

    @tool(name="list_log_groups")
    def list_log_groups_tool(region: str, name_prefix: str = "") -> str:
        """指定リージョンの CloudWatch Logs ロググループ一覧を取得する。

        Args:
            region: 対象リージョン（例: ap-northeast-1）
            name_prefix: ロググループ名の前方一致フィルタ（省略可）
        """
        return list_log_groups(config, region, name_prefix, client_factory=client_factory)

    @tool(name="query_logs")
    def query_logs_tool(region: str, log_group_names: str, query_string: str) -> str:
        """Logs Insights クエリで過去24時間（設定値）のログを検索する。

        Args:
            region: 対象リージョン（例: ap-northeast-1）
            log_group_names: 対象ロググループ名（カンマ区切りで複数指定可）
            query_string: Logs Insights のクエリ文
                （例: fields @timestamp, @message | filter @message like /ERROR/ | limit 50）
        """
        return query_logs(
            config, region, log_group_names, query_string, client_factory=client_factory
        )

    @tool(name="get_metric_statistics")
    def get_metric_statistics_tool(
        region: str,
        namespace: str,
        metric_name: str,
        dimensions_json: str = "",
        stat: str = "Average",
        period_minutes: int = 60,
    ) -> str:
        """指定メトリクスの過去24時間（設定値）の統計値を取得する。

        Args:
            region: 対象リージョン（例: ap-northeast-1）
            namespace: メトリクスの名前空間（例: AWS/Lambda）
            metric_name: メトリクス名（例: Errors）
            dimensions_json: ディメンションの JSON 配列
                （例: [{"Name": "FunctionName", "Value": "my-func"}]、省略可）
            stat: 統計の種類（Average / Sum / Maximum / Minimum / SampleCount）
            period_minutes: 集計間隔（分）
        """
        return get_metric_statistics(
            config,
            region,
            namespace,
            metric_name,
            dimensions_json,
            stat,
            period_minutes,
            client_factory=client_factory,
        )

    return [
        describe_alarms_tool,
        get_alarm_history_tool,
        list_log_groups_tool,
        query_logs_tool,
        get_metric_statistics_tool,
    ]
