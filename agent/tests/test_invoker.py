"""中継 Lambda（invoker/handler.py）のテスト。"""

import io
import json
from typing import Any

import handler as invoker


class FakeAgentCoreClient:
    def __init__(self) -> None:
        self.invoke_kwargs: dict[str, Any] = {}

    def invoke_agent_runtime(self, **kwargs: Any) -> dict[str, Any]:
        self.invoke_kwargs = kwargs
        body = json.dumps({"status": "ok", "findings_count": 2}).encode()
        return {
            "statusCode": 200,
            "contentType": "application/json",
            "response": io.BytesIO(body),
        }


def test_handlerはAgentCoreRuntimeを呼び出して結果を返す(monkeypatch: Any) -> None:
    client = FakeAgentCoreClient()
    monkeypatch.setenv(
        "AGENT_RUNTIME_ARN",
        "arn:aws:bedrock-agentcore:ap-northeast-1:123456789012:runtime/ops-agent-abc",
    )
    monkeypatch.setattr(invoker, "_create_client", lambda: client)

    result = invoker.handler({"time": "2026-08-17T23:00:00Z"}, None)

    assert result["statusCode"] == 200
    assert json.loads(result["agentResponse"])["findings_count"] == 2

    kwargs = client.invoke_kwargs
    assert kwargs["agentRuntimeArn"].endswith("runtime/ops-agent-abc")
    assert kwargs["qualifier"] == "DEFAULT"
    # セッション ID は 33 文字以上が必須
    assert len(kwargs["runtimeSessionId"]) >= 33
    payload = json.loads(kwargs["payload"])
    assert payload["trigger"] == "scheduled"
    assert payload["time"] == "2026-08-17T23:00:00Z"
