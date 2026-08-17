"""EventBridge Scheduler から起動され、AgentCore Runtime を呼び出す中継 Lambda。"""

import json
import logging
import os
import uuid
from typing import Any

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _create_client() -> Any:
    return boto3.client("bedrock-agentcore")


def handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    """AgentCore Runtime を同期呼び出しし、エージェントの応答を返す。"""
    agent_runtime_arn = os.environ["AGENT_RUNTIME_ARN"]
    qualifier = os.environ.get("QUALIFIER", "DEFAULT")

    payload = json.dumps(
        {"trigger": "scheduled", "time": (event or {}).get("time")},
        ensure_ascii=False,
    )
    client = _create_client()
    response = client.invoke_agent_runtime(
        agentRuntimeArn=agent_runtime_arn,
        qualifier=qualifier,
        # セッション ID は 33 文字以上が必須。毎回独立したセッションで実行する
        runtimeSessionId=uuid.uuid4().hex + uuid.uuid4().hex,
        payload=payload,
    )

    body = response["response"].read().decode("utf-8")
    logger.info("エージェント応答: %s", body)
    return {
        "statusCode": response.get("statusCode"),
        "contentType": response.get("contentType"),
        "agentResponse": body,
    }
