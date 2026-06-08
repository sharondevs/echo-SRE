"""Provider serialization, tool-call parsing, and error classification (httpx mocked)."""

import httpx
import pytest
import respx

from echo_sre.config import ProviderConfig
from echo_sre.inference.provider import Provider, ProviderError
from echo_sre.inference.types import Message

BASE = "https://api.test/v1"


def _provider() -> Provider:
    cfg = ProviderConfig(name="test", base_url=BASE, api_key_env=None, model="test-model")
    return Provider(cfg)


@respx.mock
@pytest.mark.asyncio
async def test_parses_tool_calls_and_usage():
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {
                                        "name": "query_metrics",
                                        "arguments": '{"query": "latency", "minutes": 30}',
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )
    )
    resp = await _provider().chat([Message(role="user", content="hi")], None, 0.1, 256)
    assert resp.message.tool_calls is not None
    tc = resp.message.tool_calls[0]
    assert tc.name == "query_metrics"
    assert tc.arguments == {"query": "latency", "minutes": 30}
    assert resp.usage.total_tokens == 15
    assert resp.provider == "test"


@respx.mock
@pytest.mark.asyncio
async def test_auth_error_is_fatal():
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(401, json={"error": {"message": "bad key"}})
    )
    with pytest.raises(ProviderError) as exc:
        await _provider().chat([Message(role="user", content="hi")], None, 0.1, 256)
    assert exc.value.retryable is False


@respx.mock
@pytest.mark.asyncio
async def test_timeout_is_retryable():
    respx.post(f"{BASE}/chat/completions").mock(side_effect=httpx.TimeoutException("slow"))
    with pytest.raises(ProviderError) as exc:
        await _provider().chat([Message(role="user", content="hi")], None, 0.1, 256)
    assert exc.value.retryable is True
