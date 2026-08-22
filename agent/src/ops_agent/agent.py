"""日次チェック・アドホック調査のオーケストレーション。

エージェント（Strands）に CloudWatch を自律調査させ、構造化出力で結果を受け取り、
整形・通知は通常のコードで行う。

失敗の扱いは 2 段構えにしている。エージェント内で捕捉できた失敗は Slack へ通知して
正常終了させ、通知すらできなかった失敗だけを例外として送出する。後者は中継 Lambda の
エラーとして CloudWatch アラームが拾う。通知済みの失敗まで例外にすると、Slack に
失敗通知とアラームが二重に届いてしまう。
"""

import logging
import time
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
    build_daily_failure_notification,
    build_failure_notification,
    build_notification,
)

logger = logging.getLogger(__name__)

# 調査方針・採点基準・レポート書式を Strands の Skills 機能で渡す（配下のスキルをすべて読み込む）
SKILLS_DIR = Path(__file__).parent / "skills"

# 構造化出力はモデルが期待どおりの応答を返さず失敗することがあるため、初回 + リトライ 2 回まで試す
STRUCTURED_OUTPUT_ATTEMPTS = 3
# 再試行の間隔（秒）。要素数は STRUCTURED_OUTPUT_ATTEMPTS - 1。
# 間を空けずに連打しても同じ結果になりやすく、スロットリングにも効かないため
RETRY_BACKOFF_SECONDS = (4, 16)


def _sleep(seconds: float) -> None:
    """再試行の待機。テストで差し替えられるよう関数に切り出す。"""
    time.sleep(seconds)


class AgentInvocationResult(Protocol):
    """エージェント呼び出しの結果のうち、オーケストレーションが使う部分。"""

    structured_output: BaseModel | None


class InvestigatorAgent(Protocol):
    """調査のオーケストレーションが必要とするエージェントのインターフェース。"""

    # 失敗した試行が残した履歴を巻き戻すために参照する
    messages: Any

    def __call__(
        self, prompt: str, *, structured_output_model: type[BaseModel] | None = None
    ) -> AgentInvocationResult: ...


def build_agent(config: Config) -> Agent:
    """CloudWatch 調査ツールと skills/ 配下のスキル
    （調査方針・採点基準・レポート書式）を持つ Strands エージェントを組み立てる。
    """
    return Agent(
        model=BedrockModel(model_id=config.model_id),
        system_prompt=build_system_prompt(config),
        tools=build_tools(config),
        plugins=[AgentSkills(skills=str(SKILLS_DIR))],
    )


def _request_structured_output[T: BaseModel](
    agent: InvestigatorAgent, output_model: type[T], prompt: str
) -> T:
    """構造化出力を 1 回だけ要求する。

    出力用のモデルをエージェントループのツールとして渡す方式を使う。構造化出力だけを
    別枠で呼ぶ方式（非推奨の `structured_output()`）は、調査中のツール呼び出しが残った
    会話履歴に対して出力用ツールだけを提示するため、モデルが調査ツールの名前を返すと
    その場で失敗していた。ループ内なら余分なツール呼び出しは実行されて先に進む。
    """
    result = agent(prompt, structured_output_model=output_model)
    output = result.structured_output
    if not isinstance(output, output_model):
        raise ValueError(f"{output_model.__name__} の構造化出力が返りませんでした")
    return output


def _structured_output[T: BaseModel](
    agent: InvestigatorAgent, output_model: type[T], prompt: str
) -> T:
    """構造化出力を取得する。失敗は会話履歴を巻き戻し、間隔を空けて再試行する。

    エージェント呼び出しはプロンプトを `agent.messages` に追加してからモデルを呼ぶため、
    失敗した試行の痕跡（依頼メッセージや応答途中の toolUse）がそのまま残る。巻き戻さずに
    再試行すると、user メッセージが連続したり toolResult を欠いた toolUse が残ったりして、
    2 回目以降が本来と無関係な ValidationException で確定的に失敗する。
    """
    history = list(agent.messages)
    for attempt in range(1, STRUCTURED_OUTPUT_ATTEMPTS):
        try:
            return _request_structured_output(agent, output_model, prompt)
        except Exception as exc:
            wait = RETRY_BACKOFF_SECONDS[attempt - 1]
            logger.warning(
                "構造化出力に失敗したため %d 秒後に再試行します（%d/%d 回目）: %s",
                wait,
                attempt,
                STRUCTURED_OUTPUT_ATTEMPTS,
                exc,
            )
            agent.messages[:] = history
            _sleep(wait)
    # 最後の 1 回は失敗をそのまま呼び出し元へ伝える
    return _request_structured_output(agent, output_model, prompt)


def run_daily_check(
    config: Config,
    *,
    agent: InvestigatorAgent | None = None,
    sns_client: Any = None,
) -> DailyReport | None:
    """調査 → 採点（構造化出力）→ 通知整形 → SNS 発行 を実行する。

    失敗したときは失敗通知を発行し、None を返す（例外は送出しない）。
    """
    if sns_client is None:
        sns_client = boto3.client("sns")

    try:
        # 組み立て自体もモデル ID やスキルの不備で失敗しうるため、通知の対象に含める
        if agent is None:
            agent = build_agent(config)
        agent(build_investigation_prompt(config))
        report = _structured_output(agent, DailyReport, DAILY_REPORT_PROMPT)
    except Exception as exc:
        # 失敗しても毎朝 1 通は届ける（死活確認を兼ねるため）。
        # Slack に出す一方で、原因追跡のために実行ログにも残す
        logger.exception("日次チェックに失敗しました")
        publish_notification(
            sns_client,
            topic_arn=config.sns_topic_arn,
            notification=build_daily_failure_notification(
                error=str(exc), generated_at=datetime.now(UTC)
            ),
        )
        return None

    notification = build_notification(
        report,
        threshold=config.score_threshold,
        generated_at=datetime.now(UTC),
        invoker_function_name=config.invoker_function_name,
        invoker_region=config.invoker_region,
    )
    publish_notification(sns_client, topic_arn=config.sns_topic_arn, notification=notification)
    return report


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
) -> AdhocReport | None:
    """Slack から届いた調査依頼を調べ、回答を通知する。

    失敗したときは失敗通知を発行し、None を返す（例外は送出しない）。
    """
    if sns_client is None:
        sns_client = boto3.client("sns")

    question = question.strip()
    if not question:
        # 中継 Lambda は SNS 権限を持たないため、空依頼を伝えられるのはここだけ
        logger.error("調査依頼の本文が空です")
        _publish_failure(sns_client, config, question, "調査依頼の本文が空です")
        return None

    try:
        # 組み立て自体もモデル ID やスキルの不備で失敗しうるため、通知の対象に含める
        if agent is None:
            agent = build_agent(config)
        agent(build_adhoc_prompt(config, question))
        report = _structured_output(agent, AdhocReport, ADHOC_REPORT_PROMPT)
    except Exception as exc:
        # 非同期実行のため、失敗を伝えないと依頼者は結果を待ち続けることになる
        logger.exception("調査依頼への回答に失敗しました")
        _publish_failure(sns_client, config, question, str(exc))
        return None

    notification = build_adhoc_notification(
        report, question=question, generated_at=datetime.now(UTC)
    )
    publish_notification(sns_client, topic_arn=config.sns_topic_arn, notification=notification)
    return report
