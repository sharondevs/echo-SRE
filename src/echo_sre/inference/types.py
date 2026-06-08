"""Provider-neutral chat/tool types shared across the inference spine and agent."""

from __future__ import annotations

from typing import AsyncIterator, Literal

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant", "tool"]


class ToolCall(BaseModel):
    """A tool/function invocation requested by the model."""

    id: str
    name: str
    arguments: dict = Field(default_factory=dict)


class Message(BaseModel):
    """A single conversation turn (provider-neutral)."""

    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] | None = None  # set on assistant turns
    tool_call_id: str | None = None  # set on tool-result turns
    name: str | None = None  # tool name on tool-result turns


class ToolSpec(BaseModel):
    """A tool the model may call, described with a JSON Schema for its arguments."""

    name: str
    description: str
    parameters: dict = Field(default_factory=lambda: {"type": "object", "properties": {}})

    def to_openai(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


class ChatResponse(BaseModel):
    """Normalized result of one chat completion, with provenance for observability."""

    message: Message
    usage: Usage = Field(default_factory=Usage)
    provider: str = ""
    model: str = ""
    latency_s: float = 0.0
    finish_reason: str = "stop"


class StreamChunk(BaseModel):
    """A streamed delta from a provider (text and/or tool-call fragments)."""

    delta_text: str = ""
    done: bool = False
    usage: Usage | None = None


# Type alias for streaming provider responses.
StreamResult = AsyncIterator[StreamChunk]
