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

# 1回の取得でエージェントのコンテキストを埋め尽くさないための上限
MAX_LOG_EVENTS = 50
MAX_LOG_STREAMS = 20
MAX_METRICS = 100
MAX_EVENT_MESSAGE_CHARS = 1000

# ログ本文の時刻はアプリケーション依存のため、API 由来の時刻とは区別して扱わせる
TIMEZONE_NOTE = (
    "timestamp は UTC。ログ本文（message）内の時刻はアプリケーション依存でタイムゾーン保証なし"
)


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _utc_iso(epoch_millis: Any) -> Any:
    """CloudWatch Logs のエポックミリ秒を UTC の ISO 形式にする。"""
    if not isinstance(epoch_millis, int):
        return epoch_millis
    return datetime.fromtimestamp(epoch_millis / 1000, UTC).isoformat()


def _region_error(config: Config, region: str) -> str | None:
    if region not in config.target_regions:
        return _dumps(
            {
                "error": f"リージョン {region} は監視対象外です。"
                f"対象: {', '.join(config.target_regions)}"
            }
        )
    return None


def _resolve_hours(config: Config, hours: int) -> int:
    """調査期間（時間）を決める。0 以下なら既定値、上限は max_lookback_hours で頭打ちにする。"""
    if hours <= 0:
        return config.lookback_hours
    return min(hours, config.max_lookback_hours)


def _time_range(config: Config, hours: int = 0) -> tuple[datetime, datetime]:
    end = datetime.now(UTC)
    return end - timedelta(hours=_resolve_hours(config, hours)), end


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
    config: Config, region: str, hours: int = 0, *, client_factory: ClientFactory = boto3.client
) -> str:
    """指定期間のアラーム状態変化の履歴を JSON 文字列で返す。"""
    if error := _region_error(config, region):
        return error
    client = client_factory("cloudwatch", region_name=region)
    start, end = _time_range(config, hours)
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
    hours: int = 0,
    *,
    client_factory: ClientFactory = boto3.client,
    timeout_seconds: float = 60.0,
    poll_interval_seconds: float = 1.0,
) -> str:
    """Logs Insights クエリを実行し、完了を待って結果を JSON 文字列で返す。"""
    if error := _region_error(config, region):
        return error
    client = client_factory("logs", region_name=region)
    start, end = _time_range(config, hours)
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
            return _dumps(
                {
                    "status": response["status"],
                    "results": response.get("results", []),
                    "timezone_note": (
                        "@timestamp と @ingestionTime は UTC。"
                        "ログ本文（@message）内の時刻はアプリケーション依存でタイムゾーン保証なし"
                    ),
                    "hours": _resolve_hours(config, hours),
                }
            )
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
    hours: int = 0,
    *,
    client_factory: ClientFactory = boto3.client,
) -> str:
    """指定メトリクスの統計値を JSON 文字列で返す。"""
    if error := _region_error(config, region):
        return error
    client = client_factory("cloudwatch", region_name=region)
    start, end = _time_range(config, hours)
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


def filter_log_events(
    config: Config,
    region: str,
    log_group_name: str,
    filter_pattern: str = "",
    hours: int = 0,
    *,
    client_factory: ClientFactory = boto3.client,
) -> str:
    """ロググループのログイベントを直接フィルタ検索して JSON 文字列で返す。"""
    if error := _region_error(config, region):
        return error
    client = client_factory("logs", region_name=region)
    start, end = _time_range(config, hours)
    kwargs: dict[str, Any] = {
        "logGroupName": log_group_name,
        "startTime": int(start.timestamp() * 1000),
        "endTime": int(end.timestamp() * 1000),
        "limit": MAX_LOG_EVENTS,
    }
    if filter_pattern:
        kwargs["filterPattern"] = filter_pattern
    response = client.filter_log_events(**kwargs)
    events = [
        {
            "timestamp": _utc_iso(event.get("timestamp")),
            "logStreamName": event.get("logStreamName"),
            "message": str(event.get("message", ""))[:MAX_EVENT_MESSAGE_CHARS],
        }
        for event in response.get("events", [])[:MAX_LOG_EVENTS]
    ]
    return _dumps({"events": events, "timezone_note": TIMEZONE_NOTE})


def describe_log_streams(
    config: Config,
    region: str,
    log_group_name: str,
    *,
    client_factory: ClientFactory = boto3.client,
) -> str:
    """ロググループ内のログストリームを最終書き込みの新しい順に JSON 文字列で返す。"""
    if error := _region_error(config, region):
        return error
    client = client_factory("logs", region_name=region)
    response = client.describe_log_streams(
        logGroupName=log_group_name,
        orderBy="LastEventTime",
        descending=True,
        limit=MAX_LOG_STREAMS,
    )
    # エポックミリ秒のまま残ると ISO 形式と混在して読み手が誤解するため、必要な項目だけ変換して返す
    streams = [
        {
            "logStreamName": stream.get("logStreamName"),
            "creationTime": _utc_iso(stream.get("creationTime")),
            "firstEventTimestamp": _utc_iso(stream.get("firstEventTimestamp")),
            "lastEventTimestamp": _utc_iso(stream.get("lastEventTimestamp")),
        }
        for stream in response.get("logStreams", [])
    ]
    return _dumps({"logStreams": streams, "timezone_note": TIMEZONE_NOTE})


def list_metrics(
    config: Config,
    region: str,
    namespace: str = "",
    metric_name: str = "",
    *,
    client_factory: ClientFactory = boto3.client,
) -> str:
    """利用可能なメトリクスとディメンションの一覧を JSON 文字列で返す。"""
    if error := _region_error(config, region):
        return error
    client = client_factory("cloudwatch", region_name=region)
    kwargs: dict[str, Any] = {}
    if namespace:
        kwargs["Namespace"] = namespace
    if metric_name:
        kwargs["MetricName"] = metric_name
    response = client.list_metrics(**kwargs)
    return _dumps({"metrics": response.get("Metrics", [])[:MAX_METRICS]})


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
    def get_alarm_history_tool(region: str, hours: int = 0) -> str:
        """指定リージョンのアラーム状態変化の履歴を取得する。

        Args:
            region: 対象リージョン（例: ap-northeast-1）
            hours: 遡る時間数。省略時は既定の調査期間（上限を超える値は上限に丸められる）
        """
        return get_alarm_history(config, region, hours, client_factory=client_factory)

    @tool(name="list_log_groups")
    def list_log_groups_tool(region: str, name_prefix: str = "") -> str:
        """指定リージョンの CloudWatch Logs ロググループ一覧を取得する。

        Args:
            region: 対象リージョン（例: ap-northeast-1）
            name_prefix: ロググループ名の前方一致フィルタ（省略可）
        """
        return list_log_groups(config, region, name_prefix, client_factory=client_factory)

    @tool(name="query_logs")
    def query_logs_tool(
        region: str, log_group_names: str, query_string: str, hours: int = 0
    ) -> str:
        """Logs Insights クエリでログを検索する。スキャン量に応じた課金が発生する。

        Args:
            region: 対象リージョン（例: ap-northeast-1）
            log_group_names: 対象ロググループ名（カンマ区切りで複数指定可）
            query_string: Logs Insights のクエリ文
                （例: fields @timestamp, @message | filter @message like /ERROR/ | limit 50）
            hours: 遡る時間数。省略時は既定の調査期間（上限を超える値は上限に丸められる）
        """
        return query_logs(
            config, region, log_group_names, query_string, hours, client_factory=client_factory
        )

    @tool(name="get_metric_statistics")
    def get_metric_statistics_tool(
        region: str,
        namespace: str,
        metric_name: str,
        dimensions_json: str = "",
        stat: str = "Average",
        period_minutes: int = 60,
        hours: int = 0,
    ) -> str:
        """指定メトリクスの統計値の推移を取得する。

        Args:
            region: 対象リージョン（例: ap-northeast-1）
            namespace: メトリクスの名前空間（例: AWS/Lambda）
            metric_name: メトリクス名（例: Errors）
            dimensions_json: ディメンションの JSON 配列
                （例: [{"Name": "FunctionName", "Value": "my-func"}]、省略可）
            stat: 統計の種類（Average / Sum / Maximum / Minimum / SampleCount）
            period_minutes: 集計間隔（分）
            hours: 遡る時間数。省略時は既定の調査期間（上限を超える値は上限に丸められる）
        """
        return get_metric_statistics(
            config,
            region,
            namespace,
            metric_name,
            dimensions_json,
            stat,
            period_minutes,
            hours,
            client_factory=client_factory,
        )

    @tool(name="filter_log_events")
    def filter_log_events_tool(
        region: str, log_group_name: str, filter_pattern: str = "", hours: int = 0
    ) -> str:
        """ロググループのログイベントをそのまま読む。前後の文脈を追う深掘りに向く。

        Logs Insights と違いクエリ課金がないため、エラー前後の生ログを確認したいときは
        query_logs よりこちらを使う。

        Args:
            region: 対象リージョン（例: ap-northeast-1）
            log_group_name: 対象ロググループ名（1つだけ指定する）
            filter_pattern: CloudWatch Logs のフィルタパターン（例: ERROR、省略可）
            hours: 遡る時間数。省略時は既定の調査期間（上限を超える値は上限に丸められる）
        """
        return filter_log_events(
            config, region, log_group_name, filter_pattern, hours, client_factory=client_factory
        )

    @tool(name="describe_log_streams")
    def describe_log_streams_tool(region: str, log_group_name: str) -> str:
        """ロググループ内のログストリームを最終書き込みの新しい順に取得する。

        Args:
            region: 対象リージョン（例: ap-northeast-1）
            log_group_name: 対象ロググループ名
        """
        return describe_log_streams(config, region, log_group_name, client_factory=client_factory)

    @tool(name="list_metrics")
    def list_metrics_tool(region: str, namespace: str = "", metric_name: str = "") -> str:
        """利用可能なメトリクスとディメンションを一覧する。

        get_metric_statistics に渡す名前やディメンションを推測せず確認したいときに使う。

        Args:
            region: 対象リージョン（例: ap-northeast-1）
            namespace: 名前空間で絞り込む（例: AWS/Lambda、省略可）
            metric_name: メトリクス名で絞り込む（例: Errors、省略可）
        """
        return list_metrics(config, region, namespace, metric_name, client_factory=client_factory)

    return [
        describe_alarms_tool,
        get_alarm_history_tool,
        list_log_groups_tool,
        query_logs_tool,
        get_metric_statistics_tool,
        filter_log_events_tool,
        describe_log_streams_tool,
        list_metrics_tool,
    ]
