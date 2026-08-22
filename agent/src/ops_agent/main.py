"""AgentCore Runtime のエントリポイント。"""

import logging
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from ops_agent.agent import run_adhoc_investigation, run_daily_check
from ops_agent.config import Config

logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload: dict[str, Any]) -> dict[str, Any]:
    """スケジュール起動なら日次チェックを、Slack からの依頼ならアドホック調査を実行する。"""
    config = Config.from_env()
    payload = payload or {}

    if payload.get("trigger") == "adhoc":
        question = str(payload.get("message", "")).strip()
        logger.info("アドホック調査を開始します: question=%s", question)
        adhoc = run_adhoc_investigation(config, question)
        # 失敗はすでに Slack へ通知済み。ここで例外にすると中継 Lambda の
        # エラーアラームが二重に鳴るため、失敗した旨を戻り値で伝えるに留める
        if adhoc is None:
            return {"status": "failed", "mode": "adhoc"}
        return {
            "status": "ok",
            "mode": "adhoc",
            "findings_count": len(adhoc.findings),
        }

    logger.info("日次チェックを開始します: payload=%s", payload)
    report = run_daily_check(config)
    if report is None:
        return {"status": "failed", "mode": "daily"}
    return {
        "status": "ok",
        "mode": "daily",
        "findings_count": len(report.findings),
        "notable_count": len(report.notable_findings(config.score_threshold)),
    }


if __name__ == "__main__":
    app.run()
