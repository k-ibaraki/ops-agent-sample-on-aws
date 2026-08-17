"""AgentCore Runtime のエントリポイント。"""

import logging
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from ops_agent.agent import run_daily_check
from ops_agent.config import Config

logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload: dict[str, Any]) -> dict[str, Any]:
    """日次チェックを実行して結果の概要を返す。"""
    logger.info("日次チェックを開始します: payload=%s", payload)
    config = Config.from_env()
    report = run_daily_check(config)
    return {
        "status": "ok",
        "findings_count": len(report.findings),
        "notable_count": len(report.notable_findings(config.score_threshold)),
    }


if __name__ == "__main__":
    app.run()
