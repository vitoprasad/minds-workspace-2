from pathlib import Path

from imbue.chat.chat_settings import RoutingMode
from imbue.chat.routing_policy import RoutingTier
from imbue.chat.routing_state import ROUTING_FILENAME
from imbue.chat.routing_state import ChatRoutingState
from imbue.chat.routing_state import read_routing_state
from imbue.chat.routing_state import write_routing_state


def test_only_auto_routes_a_chat() -> None:
    assert ChatRoutingState(mode=RoutingMode.AUTO).is_routed is True
    assert ChatRoutingState(mode=RoutingMode.OFF).is_routed is False


def test_the_routing_file_reads_back_what_was_written(tmp_path: Path) -> None:
    state = ChatRoutingState(
        mode=RoutingMode.AUTO, tier=RoutingTier.COMPLEX, exhausted_accounts=("one", "two")
    )

    write_routing_state(tmp_path / "chats" / "agent-1", state)

    assert read_routing_state(tmp_path / "chats" / "agent-1") == state
    assert (tmp_path / "chats" / "agent-1" / ROUTING_FILENAME).is_file()


def test_a_chat_without_a_routing_file_or_with_an_unreadable_one_reads_as_none(tmp_path: Path) -> None:
    assert read_routing_state(tmp_path / "nowhere") is None
    (tmp_path / ROUTING_FILENAME).write_text("{not json")
    assert read_routing_state(tmp_path) is None
    (tmp_path / ROUTING_FILENAME).write_text('{"mode": "sometimes"}')
    assert read_routing_state(tmp_path) is None


def test_a_file_written_before_the_chat_had_learned_anything_still_reads(tmp_path: Path) -> None:
    (tmp_path / ROUTING_FILENAME).write_text('{"mode": "auto"}')
    assert read_routing_state(tmp_path) == ChatRoutingState(mode=RoutingMode.AUTO)
