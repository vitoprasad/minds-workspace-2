import httpx
import pytest

from chat_bridge.upstream import UpstreamError
from chat_bridge.upstream import _derive_activity
from chat_bridge.upstream import _detail_or_status
from chat_bridge.upstream import select_agent

_AGENTS = [
    {"id": "agent-1", "name": "alpha", "state": "RUNNING"},
    {"id": "agent-2", "name": "Beta", "state": "WAITING"},
    {"id": "agent-3", "name": "beta", "state": "RUNNING"},
]


def test_select_agent_matches_exact_id() -> None:
    assert select_agent(_AGENTS, "agent-2") == "agent-2"


def test_select_agent_matches_exact_name() -> None:
    assert select_agent(_AGENTS, "alpha") == "agent-1"


def test_select_agent_matches_name_case_insensitively_when_unambiguous() -> None:
    assert select_agent([_AGENTS[0], _AGENTS[1]], "BETA") == "agent-2"


def test_select_agent_rejects_ambiguous_name_with_409() -> None:
    # "BETA" has no exact match, so the case-insensitive pass matches both
    # "Beta" and "beta" -- ambiguous.
    with pytest.raises(UpstreamError) as caught:
        select_agent(_AGENTS, "BETA")
    assert caught.value.status_code == 409


def test_select_agent_rejects_unknown_with_404() -> None:
    with pytest.raises(UpstreamError) as caught:
        select_agent(_AGENTS, "nope")
    assert caught.value.status_code == 404


def test_detail_or_status_prefers_upstream_detail() -> None:
    response = httpx.Response(500, json={"detail": "boom"})
    assert _detail_or_status(response) == "boom"


def test_detail_or_status_falls_back_to_status_for_non_json() -> None:
    response = httpx.Response(503, text="upstream down")
    assert "503" in _detail_or_status(response)


def test_derive_activity_empty_tail_is_idle() -> None:
    assert _derive_activity([]) == "idle"


def test_derive_activity_trailing_assistant_reply_is_idle() -> None:
    events = [
        {"type": "user_message", "content": "hi"},
        {"type": "assistant_message", "text": "hello", "tool_calls": []},
    ]
    assert _derive_activity(events) == "idle"


def test_derive_activity_trailing_user_message_is_thinking() -> None:
    assert _derive_activity([{"type": "user_message", "content": "do a thing"}]) == "thinking"


def test_derive_activity_trailing_tool_result_is_thinking() -> None:
    events = [
        {"type": "assistant_message", "text": "", "tool_calls": [{"tool_call_id": "t1", "tool_name": "Bash"}]},
        {"type": "tool_result", "tool_call_id": "t1", "tool_name": "Bash"},
    ]
    assert _derive_activity(events) == "thinking"


def test_derive_activity_unmatched_tool_call_is_tool_running() -> None:
    events = [
        {"type": "user_message", "content": "go"},
        {"type": "assistant_message", "text": "", "tool_calls": [{"tool_call_id": "t1", "tool_name": "Bash"}]},
    ]
    assert _derive_activity(events) == "tool_running"
