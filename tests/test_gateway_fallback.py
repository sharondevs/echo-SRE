"""The headline behavior: priority routing with automatic provider fallback."""

from types import SimpleNamespace

import pytest

from echo_sre.config import GatewayDefaults
from echo_sre.inference import metrics as M
from echo_sre.inference.gateway import AllProvidersFailed, InferenceGateway
from echo_sre.inference.provider import ProviderError
from echo_sre.inference.types import ChatResponse, Message


class FakeProvider:
    def __init__(self, name: str, behavior: str):
        # behavior: "good" | "retryable" | "fatal"
        self.name = name
        self.behavior = behavior
        self.calls = 0
        self.cfg = SimpleNamespace(model=name, max_context_tokens=8192)

    async def chat(self, messages, tools, temperature, max_tokens):
        self.calls += 1
        if self.behavior == "good":
            return ChatResponse(
                message=Message(role="assistant", content="ok"), provider=self.name, model=self.name
            )
        raise ProviderError(self.name, "boom", retryable=(self.behavior == "retryable"))


def _counter(metric, **labels) -> float:
    return metric.labels(**labels)._value.get()


@pytest.mark.asyncio
async def test_fails_over_to_healthy_provider():
    retryable = FakeProvider("p_retry", "retryable")
    fatal = FakeProvider("p_fatal", "fatal")
    good = FakeProvider("p_good", "good")
    defaults = GatewayDefaults(retries_per_provider=1, backoff_base_s=0.0)
    gw = InferenceGateway([retryable, fatal, good], defaults)

    before = _counter(M.LLM_FALLBACKS, from_provider="p_retry", to_provider="p_fatal")
    resp = await gw.chat([Message(role="user", content="hi")])

    assert resp.message.content == "ok"
    assert resp.provider == "p_good"
    assert retryable.calls == 2  # initial try + 1 retry (it is retryable)
    assert fatal.calls == 1  # fatal errors are NOT retried
    assert good.calls == 1
    after = _counter(M.LLM_FALLBACKS, from_provider="p_retry", to_provider="p_fatal")
    assert after == before + 1  # a failover was recorded


@pytest.mark.asyncio
async def test_all_providers_failed_raises():
    gw = InferenceGateway(
        [FakeProvider("a", "fatal"), FakeProvider("b", "fatal")],
        GatewayDefaults(retries_per_provider=0, backoff_base_s=0.0),
    )
    with pytest.raises(AllProvidersFailed):
        await gw.chat([Message(role="user", content="hi")])


@pytest.mark.asyncio
async def test_empty_chain_uses_mock_model():
    gw = InferenceGateway([])  # no usable providers -> offline mock
    resp = await gw.chat([Message(role="user", content="investigate latency alert")])
    assert resp.provider == "mock"
