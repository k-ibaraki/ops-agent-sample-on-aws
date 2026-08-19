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


def test_クライアントは長時間実行を待てるリトライなし設定になっている(monkeypatch: Any) -> None:
    # ローカルの AWS 設定に依存しないよう、ダミーの認証情報で隔離する
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_CONFIG_FILE", "/dev/null")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/dev/null")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    client = invoker._create_client()

    config = client.meta.config
    assert config.read_timeout == 840
    assert config.retries["total_max_attempts"] == 1


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


def test_handlerはSlackからの調査依頼をそのまま引き渡す(monkeypatch: Any) -> None:
    client = FakeAgentCoreClient()
    monkeypatch.setenv(
        "AGENT_RUNTIME_ARN",
        "arn:aws:bedrock-agentcore:ap-northeast-1:123456789012:runtime/ops-agent-abc",
    )
    monkeypatch.setattr(invoker, "_create_client", lambda: client)

    invoker.handler({"trigger": "adhoc", "message": "昨日のエラーを詳しく"}, None)

    payload = json.loads(client.invoke_kwargs["payload"])
    assert payload["trigger"] == "adhoc"
    assert payload["message"] == "昨日のエラーを詳しく"


def test_handlerは依頼内容が空なら日次チェックに落とさず中止する(monkeypatch: Any) -> None:
    client = FakeAgentCoreClient()
    monkeypatch.setenv(
        "AGENT_RUNTIME_ARN",
        "arn:aws:bedrock-agentcore:ap-northeast-1:123456789012:runtime/ops-agent-abc",
    )
    monkeypatch.setattr(invoker, "_create_client", lambda: client)

    # 空依頼で日次チェックが走ると、重複通知と余計な課金になる
    result = invoker.handler({"trigger": "adhoc", "message": "   "}, None)

    assert result["statusCode"] == 400
    assert client.invoke_kwargs == {}
