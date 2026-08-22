"""日次チェックのオーケストレーション。

エージェント（Strands）に CloudWatch を自律調査させ、構造化出力で採点結果を受け取り、
整形・通知は通常のコードで行う。
"""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import boto3
from pydantic import BaseModel
from strands import Agent, AgentSkills
from strands.models import BedrockModel

from ops_agent.aws_tools import build_tools
from ops_agent.config import Config
from ops_agent.models import AdhocReport, DailyReport
from ops_agent.notifier import publish_notification
from ops_agent.prompts import (
    ADHOC_REPORT_PROMPT,
    DAILY_REPORT_PROMPT,
    build_adhoc_prompt,
    build_investigation_prompt,
    build_system_prompt,
)
from ops_agent.report import (
    build_adhoc_notification,
    build_failure_notification,
    build_notification,
)

logger = logging.getLogger(__name__)

# 調査方針・採点基準・レポート書式を Strands の Skills 機能で渡す（配下のスキルをすべて読み込む）
SKILLS_DIR = Path(__file__).parent / "skills"

# 構造化出力はモデルがツール呼び出しを返さず失敗することがあるため、初回 + リトライ 2 回まで試す
STRUCTURED_OUTPUT_ATTEMPTS = 3


class InvestigatorAgent(Protocol):
    """調査のオーケストレーションが必要とするエージェントのインターフェース。"""

    def __call__(self, prompt: str) -> Any: ...

    def structured_output[T: BaseModel](self, output_model: type[T], prompt: str) -> T: ...


def build_agent(config: Config) -> Agent:
    """CloudWatch 調査ツールと書式スキルを持つ Strands エージェントを組み立てる。"""
    return Agent(
        model=BedrockModel(model_id=config.model_id),
        system_prompt=build_system_prompt(config),
        tools=build_tools(config),
        plugins=[AgentSkills(skills=str(SKILLS_DIR))],
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
    report = _structured_output(agent, DailyReport, DAILY_REPORT_PROMPT)

    notification = build_notification(
        report,
        threshold=config.score_threshold,
        generated_at=datetime.now(UTC),
        invoker_function_name=config.invoker_function_name,
        invoker_region=config.invoker_region,
    )
    publish_notification(sns_client, topic_arn=config.sns_topic_arn, notification=notification)
    return report


def _structured_output[T: BaseModel](
    agent: InvestigatorAgent, output_model: type[T], prompt: str
) -> T:
    """構造化出力を取得する。失敗は再試行する（会話履歴は変更されないため安全）。"""
    for attempt in range(1, STRUCTURED_OUTPUT_ATTEMPTS):
        try:
            return agent.structured_output(output_model, prompt)
        except Exception as exc:
            logger.warning(
                "構造化出力に失敗したため再試行します（%d/%d 回目）: %s",
                attempt,
                STRUCTURED_OUTPUT_ATTEMPTS,
                exc,
            )
    # 最後の 1 回は失敗をそのまま呼び出し元へ伝える
    return agent.structured_output(output_model, prompt)


def _publish_failure(sns_client: Any, config: Config, question: str, error: str) -> None:
    """調査依頼に応えられなかったことを依頼者に伝える。"""
    publish_notification(
        sns_client,
        topic_arn=config.sns_topic_arn,
        notification=build_failure_notification(
            question=question, error=error, generated_at=datetime.now(UTC)
        ),
    )


def run_adhoc_investigation(
    config: Config,
    question: str,
    *,
    agent: InvestigatorAgent | None = None,
    sns_client: Any = None,
) -> AdhocReport:
    """Slack から届いた調査依頼を調べ、回答を通知する。"""
    if sns_client is None:
        sns_client = boto3.client("sns")

    question = question.strip()
    if not question:
        # 中継 Lambda は SNS 権限を持たないため、空依頼を伝えられるのはここだけ
        _publish_failure(sns_client, config, question, "調査依頼の本文が空です")
        raise ValueError("調査依頼の本文が空です")

    try:
        # 組み立て自体もモデル ID やスキルの不備で失敗しうるため、通知の対象に含める
        if agent is None:
            agent = build_agent(config)
        agent(build_adhoc_prompt(config, question))
        report = _structured_output(agent, AdhocReport, ADHOC_REPORT_PROMPT)
    except Exception as exc:
        # 非同期実行のため、失敗を伝えないと依頼者は結果を待ち続けることになる
        _publish_failure(sns_client, config, question, str(exc))
        raise

    notification = build_adhoc_notification(
        report, question=question, generated_at=datetime.now(UTC)
    )
    publish_notification(sns_client, topic_arn=config.sns_topic_arn, notification=notification)
    return report
