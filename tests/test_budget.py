from echo_sre.inference.budget import total_tokens, trim_to_budget
from echo_sre.inference.types import Message, ToolCall


def _msg(role, content, **kw):
    return Message(role=role, content=content, **kw)


def test_trim_keeps_system_and_recent_and_fits_budget():
    system = _msg("system", "S" * 400)  # ~100 tokens
    history = [_msg("user" if i % 2 == 0 else "assistant", f"turn {i} " + "x" * 200) for i in range(20)]
    messages = [system] + history

    trimmed = trim_to_budget(messages, max_context=400, reserve_output=100)

    assert trimmed[0].role == "system"  # system always retained
    assert total_tokens(trimmed) <= 400 - 100
    # The most-recent turn survives; the oldest is dropped.
    assert trimmed[-1].content == history[-1].content
    assert len(trimmed) < len(messages)


def test_trim_does_not_orphan_tool_results():
    system = _msg("system", "sys")
    assistant = _msg("assistant", None, tool_calls=[ToolCall(id="c1", name="query_metrics", arguments={})])
    tool_result = _msg("tool", "x" * 50, tool_call_id="c1", name="query_metrics")
    # Pad with old turns so trimming is forced.
    old = [_msg("user", "y" * 300) for _ in range(10)]
    messages = [system] + old + [assistant, tool_result]

    trimmed = trim_to_budget(messages, max_context=300, reserve_output=80)

    tool_msgs = [m for m in trimmed if m.role == "tool"]
    # Any surviving tool result must still have its triggering assistant tool_call present.
    for tm in tool_msgs:
        assert any(
            m.role == "assistant" and m.tool_calls and any(tc.id == tm.tool_call_id for tc in m.tool_calls)
            for m in trimmed
        )
