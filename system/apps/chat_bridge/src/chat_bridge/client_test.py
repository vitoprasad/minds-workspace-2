import pytest

from chat_bridge.client import ChatBridgeClient
from chat_bridge.client import ChatBridgeError
from chat_bridge.client import _render_event
from chat_bridge.client import _try_parse


def test_client_requires_url() -> None:
    with pytest.raises(ChatBridgeError):
        ChatBridgeClient("", "token")


def test_client_requires_token() -> None:
    with pytest.raises(ChatBridgeError):
        ChatBridgeClient("http://x", "")


def test_client_strips_trailing_slash() -> None:
    client = ChatBridgeClient("http://host/service/chat-bridge/", "t")
    assert client._base == "http://host/service/chat-bridge"


def test_try_parse_returns_dict_or_none() -> None:
    assert _try_parse('{"a": 1}') == {"a": 1}
    assert _try_parse("not json") is None
    assert _try_parse("[1, 2]") is None


def test_render_user_message() -> None:
    rendered = _render_event({"type": "user_message", "content": "hi", "timestamp": "2026-01-01T00:00:00Z"})
    assert "YOU: hi" in rendered


def test_render_assistant_message_with_text() -> None:
    rendered = _render_event({"type": "assistant_message", "text": "hello", "timestamp": ""})
    assert "AGENT: hello" in rendered


def test_render_assistant_tool_only_turn_names_tools() -> None:
    event = {"type": "assistant_message", "text": "", "tool_calls": [{"tool_name": "Bash"}]}
    rendered = _render_event(event)
    assert "Bash" in rendered and "ran" in rendered


def test_render_tool_result() -> None:
    rendered = _render_event({"type": "tool_result", "tool_name": "Read"})
    assert "Read" in rendered
