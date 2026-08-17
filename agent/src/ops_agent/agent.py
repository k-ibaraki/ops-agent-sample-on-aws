"""日次チェックのオーケストレーション。

エージェント（Strands）に CloudWatch を自律調査させ、構造化出力で採点結果を受け取り、
整形・通知は通常のコードで行う。
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import boto3
from strands import Agent, AgentSkills
from strands.models import BedrockModel

from ops_agent.aws_tools import build_tools
from ops_agent.config import Config
from ops_agent.models import DailyReport
from ops_agent.notifier import publish_notification
from ops_agent.report import build_notification

# レポート文面の書式ルールを Strands の Skills 機能で渡す
SKILLS_DIR = Path(__file__).parent / "skills"


class InvestigatorAgent(Protocol):
    """run_daily_check が必要とするエージェントのインターフェース。"""

    def __call__(self, prompt: str) -> Any: ...

    def structured_output(self, output_model: type[DailyReport], prompt: str) -> DailyReport: ...


def build_system_prompt(config: Config) -> str:
    return f"""あなたは AWS アカウントの運用監視を担当する SRE エージェントです。
CloudWatch の調査ツールを使って過去 {config.lookback_hours} 時間の状態を調査し、
気になる問題を見つけて採点してください。

## 採点基準（0〜100 点）
以下の 3 つの観点を総合して採点します。
- 影響範囲: どれだけ多くのリソース・ユーザーに影響するか
- 緊急度: 今すぐ対応しないとどうなるか
- 継続性: 一過性のスパイクか、継続・悪化しているか

目安:
- 90〜100: 重大な障害が進行中（即時対応が必要）
- 70〜89: 早急な対応が必要な問題
- 50〜69: 注意が必要な問題（当日中に確認すべき）
- 25〜49: 軽微な問題（余裕があるときに確認）
- 0〜24: 情報レベル（対応不要の可能性が高い）

## 調査の方針
- まずアラームの状態と履歴で全体を把握し、怪しい箇所をログとメトリクスで深掘りする
- 事実（ツールの結果）に基づいて判断し、推測で問題をでっち上げない
- 問題が見つからなければ、無理に問題を作らず「異常なし」と報告する
- 出力はすべて日本語で書く"""


def build_investigation_prompt(config: Config) -> str:
    regions = ", ".join(config.target_regions)
    return f"""過去 {config.lookback_hours} 時間の AWS アカウントの状態を調査してください。

対象リージョン: {regions}

手順:
1. 各リージョンのアラーム状態（describe_alarms）と履歴（get_alarm_history）を確認する
2. ロググループを確認し（list_log_groups）、怪しい箇所はエラーログを検索する（query_logs）
3. 必要に応じてメトリクスの推移を確認する（get_metric_statistics）
4. 見つけた問題を採点基準に沿って採点し、根拠と推奨アクションを整理する"""


def build_agent(config: Config) -> Agent:
    """CloudWatch 調査ツールと書式スキルを持つ Strands エージェントを組み立てる。"""
    return Agent(
        model=BedrockModel(model_id=config.model_id),
        system_prompt=build_system_prompt(config),
        tools=build_tools(config),
        plugins=[AgentSkills(skills=str(SKILLS_DIR / "slack-report-style"))],
    )


def run_daily_check(
    config: Config,
    *,
    agent: InvestigatorAgent | None = None,
    sns_client: Any = None,
) -> DailyReport:
    """調査 → 採点（構造化出力）→ 通知整形 → SNS 発行 を実行する。"""
    if agent is None:
        agent = build_agent(config)
    if sns_client is None:
        sns_client = boto3.client("sns")

    agent(build_investigation_prompt(config))
    report = agent.structured_output(
        DailyReport,
        "ここまでの調査結果を DailyReport にまとめてください。"
        "問題ごとに採点基準に沿ったスコアを付けてください。"
        "文面は slack-report-style スキルを読み込み、その書式ルールに従って書いてください。",
    )

    notification = build_notification(
        report, threshold=config.score_threshold, generated_at=datetime.now(UTC)
    )
    publish_notification(sns_client, topic_arn=config.sns_topic_arn, notification=notification)
    return report
