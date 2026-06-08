"""Token budgeting / context-window trimming.

A tokenizer-free heuristic keeps the dependency surface small. The goal is to never
exceed a provider's context window: always keep the system prompt and the most-recent
turns, dropping the oldest turns first — and never splitting a tool call from its
matching tool result (they are dropped as a pair).
"""

from __future__ import annotations

from .metrics import CONTEXT_TRIMS
from .types import Message

# Rough chars-per-token; deliberately conservative so we under-fill rather than overflow.
_CHARS_PER_TOKEN = 4
_PER_MESSAGE_OVERHEAD = 4  # role/formatting tokens per message


def estimate_tokens(text: str | None) -> int:
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN)


def message_tokens(m: Message) -> int:
    total = _PER_MESSAGE_OVERHEAD + estimate_tokens(m.content)
    if m.tool_calls:
        for tc in m.tool_calls:
            total += estimate_tokens(tc.name) + estimate_tokens(str(tc.arguments)) + 4
    if m.name:
        total += estimate_tokens(m.name)
    return total


def total_tokens(messages: list[Message]) -> int:
    return sum(message_tokens(m) for m in messages)


def trim_to_budget(
    messages: list[Message], max_context: int, reserve_output: int
) -> list[Message]:
    """Return a copy of ``messages`` that fits within ``max_context - reserve_output``.

    System messages are always retained. Oldest non-system turns are dropped first.
    A tool-result message is never separated from the assistant tool-call that produced
    it: if an assistant turn with tool_calls would be dropped, its following tool
    results are dropped with it (and vice-versa).
    """
    budget = max(256, max_context - reserve_output)
    if total_tokens(messages) <= budget:
        return list(messages)

    system = [m for m in messages if m.role == "system"]
    rest = [m for m in messages if m.role != "system"]

    # Walk from newest to oldest, keeping turns until the budget is exhausted.
    kept_rev: list[Message] = []
    running = total_tokens(system)
    for m in reversed(rest):
        cost = message_tokens(m)
        if running + cost > budget and kept_rev:
            break
        running += cost
        kept_rev.append(m)
    kept = list(reversed(kept_rev))

    # Repair orphaned tool results whose triggering assistant turn was dropped.
    valid_call_ids = {
        tc.id for m in kept if m.role == "assistant" and m.tool_calls for tc in m.tool_calls
    }
    kept = [m for m in kept if not (m.role == "tool" and m.tool_call_id not in valid_call_ids)]

    CONTEXT_TRIMS.inc()
    return system + kept
