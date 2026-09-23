from pathlib import Path

import pytest
from inbox_store import build_inbox_paths
from inbox_store import load_conversation_id
from inbox_store import load_state
from inbox_store import persist_raw_request
from inbox_store import write_configuration
from inbox_store import write_state
from inbox_types import InboxNotConfiguredError
from inbox_types import InboxRequest
from inbox_types import InboxState
from inbox_types import InboxStateError
from inbox_types import SlackConversationId
from inbox_types import SlackTimestamp
from inbox_types import SlackUserId


def test_configuration_round_trips(tmp_path: Path) -> None:
    paths = build_inbox_paths(root_directory=tmp_path)
    write_configuration(paths=paths, conversation_id=SlackConversationId("D_SELF"))

    assert load_conversation_id(paths) == SlackConversationId("D_SELF")


def test_state_round_trips(tmp_path: Path) -> None:
    paths = build_inbox_paths(root_directory=tmp_path)
    state = InboxState(
        oldest_ts=SlackTimestamp("100.000000"),
        handled_ts=(SlackTimestamp("101.000000"),),
        posted_ts=(SlackTimestamp("102.000000"),),
        watched_thread_ts=(SlackTimestamp("101.000000"),),
    )
    write_state(paths=paths, state=state)

    assert load_state(paths) == state


def test_a_missing_configuration_is_reported_as_unconfigured(tmp_path: Path) -> None:
    with pytest.raises(InboxNotConfiguredError):
        load_conversation_id(build_inbox_paths(root_directory=tmp_path))


def test_a_corrupt_configuration_raises_rather_than_guessing(tmp_path: Path) -> None:
    """A broken config must never quietly resolve to some other conversation."""
    paths = build_inbox_paths(root_directory=tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    paths.configuration_path.write_text("conversation_id = \n")

    with pytest.raises(InboxStateError):
        load_conversation_id(paths)


def test_a_raw_record_is_archived_with_its_source_link(tmp_path: Path) -> None:
    paths = build_inbox_paths(root_directory=tmp_path)
    request = InboxRequest(
        request_ts=SlackTimestamp("600.000000"),
        thread_ts=SlackTimestamp("600.000000"),
        author_user_id=SlackUserId("U_USER"),
        text="what is on tomorrow",
        permalink="https://slack.test/archives/D_SELF/p600000000",
        raw_message={"ts": "600.000000", "text": "what is on tomorrow", "user": "U_USER"},
    )

    record_path = persist_raw_request(paths=paths, request=request)

    assert record_path.exists()
    assert "https://slack.test/archives/D_SELF/p600000000" in record_path.read_text()
