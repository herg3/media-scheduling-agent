import importlib.util
import json

import httpx
import pytest


def model_class():
    assert importlib.util.find_spec("scheduling_agent.model") is not None, "Model adapter missing"
    from scheduling_agent.model import LiveModel
    return LiveModel


def test_live_adapter_parses_real_tool_call_protocol():
    payload = {"choices": [{"message": {"tool_calls": [{
        "id": "actual-provider-call", "type": "function", "function": {
            "name": "read_material",
            "arguments": json.dumps({"material_id": "M01", "decision_summary": "Read metadata"})}}]}}],
               "usage": {"prompt_tokens": 10, "completion_tokens": 12, "total_tokens": 22}}
    def handler(request):
        body = json.loads(request.content)
        assert body["tools"] == [{"type": "function"}]
        assert body["messages"][0]["content"] == "synthetic"
        assert request.headers["Authorization"] == "Bearer test-only"
        return httpx.Response(200, json=payload)
    model = model_class()("test-only", "https://test.invalid/v1", "test-model",
                         transport=httpx.MockTransport(handler))
    action = model.next_action([{"role": "user", "content": "synthetic"}], [{"type": "function"}], 3)
    assert action.call_id == "actual-provider-call"
    assert action.tool == "read_material"
    assert model.usage["total_tokens"] == 22


def test_provider_error_does_not_expose_secret():
    def handler(request):
        return httpx.Response(401, text="test-only-sensitive")
    model = model_class()("test-only-sensitive", "https://test.invalid/v1", "test-model",
                         transport=httpx.MockTransport(handler))
    from scheduling_agent.contracts import DomainError
    with pytest.raises(DomainError) as error:
        model.next_action([], [], 3)
    assert error.value.code == "MODEL_UNAVAILABLE"
    assert "test-only-sensitive" not in str(error.value)
