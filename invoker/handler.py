"""EventBridge Scheduler から起動され、AgentCore Runtime を呼び出す中継 Lambda。"""

import json
import logging
import os
import uuid
from typing import Any

import boto3
from botocore.config import Config

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _create_client() -> Any:
    return boto3.client(
        "bedrock-agentcore",
        config=Config(
            connect_timeout=10,
            # エージェントの調査完了まで同期で待つ（Lambda タイムアウト 15 分より少し短く）
            read_timeout=840,
            # タイムアウト時のリトライはエージェントの多重実行（重複通知）になるため無効化
            retries={"total_max_attempts": 1},
        ),
    )


def _build_payload(event: dict[str, Any]) -> dict[str, Any] | None:
    """イベントからエージェントへ渡すペイロードを組み立てる。中止すべきなら None を返す。"""
    if event.get("trigger") != "adhoc":
        return {"trigger": "scheduled", "time": event.get("time")}

    # 依頼内容が空のまま日次チェックに落とすと、重複通知と余計な課金になる
    message = str(event.get("message", "")).strip()
    if not message:
        return None
    return {"trigger": "adhoc", "message": message}


def handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    """AgentCore Runtime を同期呼び出しし、エージェントの応答を返す。"""
    agent_runtime_arn = os.environ["AGENT_RUNTIME_ARN"]
    qualifier = os.environ.get("QUALIFIER", "DEFAULT")

    payload_body = _build_payload(event or {})
    if payload_body is None:
        logger.warning("調査依頼の本文が空のため中止します")
        return {"statusCode": 400, "error": "調査依頼の本文が空です"}

    payload = json.dumps(payload_body, ensure_ascii=False)
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
