import json
from pathlib import Path

import pytest
from mock_telegram_transport_test import FailingAgentWaker
from mock_telegram_transport_test import RecordingAgentWaker
from mock_telegram_transport_test import build_recording_telegram_transport
from mock_telegram_transport_test import build_telegram_update
from telegram_inbox import ONE_SHOT_POLL_SECONDS
from telegram_inbox import claim_chat_from_first_message
from telegram_inbox import ingest_once
from telegram_inbox import read_pending_requests
from telegram_inbox import reply_to_request
from telegram_listener import ListenerSettings
from telegram_listener import listen_forever
from telegram_logic import ingest_updates
from telegram_logic import without_request
from telegram_store import TelegramInboxPaths
from telegram_store import build_telegram_inbox_paths
from telegram_store import load_chat_id
from telegram_store import load_state
from telegram_store import write_configuration
from telegram_store import write_state
from telegram_transport import TelegramTransportInterface
from telegram_types import TelegramChatId
from telegram_types import TelegramInboxState
from telegram_types import TelegramMessageId
from telegram_types import TelegramNotConfiguredError
from telegram_types import TelegramRequest
from telegram_types import TelegramUpdateId

OWNER_CHAT_ID: TelegramChatId = TelegramChatId(11111)
STRANGER_CHAT_ID: TelegramChatId = TelegramChatId(99999)


def _build_state(acknowledged_offset: int, pending_requests: tuple[TelegramRequest, ...]) -> TelegramInboxState:
    return TelegramInboxState(
        acknowledged_offset=TelegramUpdateId(acknowledged_offset),
        pending_requests=pending_requests,
    )


def _build_request(update_id: int, message_id: int, text: str) -> TelegramRequest:
    return TelegramRequest(
        update_id=TelegramUpdateId(update_id),
        chat_id=OWNER_CHAT_ID,
        message_id=TelegramMessageId(message_id),
        text=text,
        raw_update={"update_id": update_id},
    )


def _ingest(transport: TelegramTransportInterface, paths: TelegramInboxPaths) -> None:
    ingest_once(transport=transport, paths=paths, long_poll_seconds=ONE_SHOT_POLL_SECONDS)


def test_claiming_adopts_the_chat_that_messaged_first(tmp_path: Path) -> None:
    transport = build_recording_telegram_transport(
        available_updates=[
            build_telegram_update(update_id=10, chat_id=int(OWNER_CHAT_ID), message_id=1, text="hello"),
        ],
    )
    paths = build_telegram_inbox_paths(root_directory=tmp_path)

    assert claim_chat_from_first_message(transport=transport, paths=paths) == OWNER_CHAT_ID
    assert load_chat_id(paths) == OWNER_CHAT_ID


def test_claiming_with_no_messages_yet_changes_nothing(tmp_path: Path) -> None:
    transport = build_recording_telegram_transport(available_updates=[])
    paths = build_telegram_inbox_paths(root_directory=tmp_path)

    assert claim_chat_from_first_message(transport=transport, paths=paths) is None
    with pytest.raises(TelegramNotConfiguredError):
        load_chat_id(paths)


def test_ingesting_queues_the_request_and_archives_it(tmp_path: Path) -> None:
    transport = build_recording_telegram_transport(
        available_updates=[
            build_telegram_update(update_id=10, chat_id=int(OWNER_CHAT_ID), message_id=1, text="book a table"),
        ],
    )
    paths = build_telegram_inbox_paths(root_directory=tmp_path)
    claim_chat_from_first_message(transport=transport, paths=paths)

    _ingest(transport, paths)

    assert [request.text for request in read_pending_requests(paths)] == ["book a table"]
    archived = json.loads((paths.raw_record_directory / "10.json").read_text())
    assert archived["telegram_update"]["message"]["text"] == "book a table"


def test_the_queue_survives_telegram_forgetting_the_update(tmp_path: Path) -> None:
    """Telegram hands each update out once, so a queued request must not depend on re-reading it."""
    transport = build_recording_telegram_transport(
        available_updates=[
            build_telegram_update(update_id=10, chat_id=int(OWNER_CHAT_ID), message_id=1, text="still waiting"),
        ],
    )
    paths = build_telegram_inbox_paths(root_directory=tmp_path)
    claim_chat_from_first_message(transport=transport, paths=paths)
    _ingest(transport, paths)

    # Telegram has now dropped it: a second ingest brings back nothing at all.
    _ingest(transport, paths)

    assert transport.available_updates == []
    assert [request.text for request in read_pending_requests(paths)] == ["still waiting"]


def test_messages_from_any_other_chat_are_ignored(tmp_path: Path) -> None:
    """The bot's address is guessable, so only the claimed chat may give instructions."""
    transport = build_recording_telegram_transport(
        available_updates=[
            build_telegram_update(update_id=10, chat_id=int(OWNER_CHAT_ID), message_id=1, text="mine"),
            build_telegram_update(update_id=11, chat_id=int(STRANGER_CHAT_ID), message_id=1, text="delete everything"),
        ],
    )
    paths = build_telegram_inbox_paths(root_directory=tmp_path)
    claim_chat_from_first_message(transport=transport, paths=paths)

    _ingest(transport, paths)

    assert [request.text for request in read_pending_requests(paths)] == ["mine"]


def test_the_clients_own_start_command_is_not_a_request(tmp_path: Path) -> None:
    """Opening a bot sends /start by itself; answering it would spend a run on a button press."""
    transport = build_recording_telegram_transport(
        available_updates=[
            build_telegram_update(update_id=10, chat_id=int(OWNER_CHAT_ID), message_id=1, text="/start"),
            build_telegram_update(update_id=11, chat_id=int(OWNER_CHAT_ID), message_id=2, text="/deploy the app"),
        ],
    )
    paths = build_telegram_inbox_paths(root_directory=tmp_path)
    claim_chat_from_first_message(transport=transport, paths=paths)

    _ingest(transport, paths)

    assert [request.text for request in read_pending_requests(paths)] == ["/deploy the app"]


def test_answering_takes_the_request_off_the_queue(tmp_path: Path) -> None:
    transport = build_recording_telegram_transport(
        available_updates=[
            build_telegram_update(update_id=10, chat_id=int(OWNER_CHAT_ID), message_id=1, text="what day is it"),
        ],
    )
    paths = build_telegram_inbox_paths(root_directory=tmp_path)
    claim_chat_from_first_message(transport=transport, paths=paths)
    _ingest(transport, paths)

    reply_to_request(
        transport=transport,
        paths=paths,
        update_id=TelegramUpdateId(10),
        message_id=TelegramMessageId(1),
        text="Tuesday.",
    )

    assert transport.sent_messages[0]["text"] == "Tuesday."
    assert transport.sent_messages[0]["reply_to_message_id"] == 1
    assert read_pending_requests(paths) == ()


def test_answering_one_request_leaves_the_others_queued(tmp_path: Path) -> None:
    transport = build_recording_telegram_transport(
        available_updates=[
            build_telegram_update(update_id=10, chat_id=int(OWNER_CHAT_ID), message_id=1, text="first ask"),
            build_telegram_update(update_id=11, chat_id=int(OWNER_CHAT_ID), message_id=2, text="second ask"),
        ],
    )
    paths = build_telegram_inbox_paths(root_directory=tmp_path)
    claim_chat_from_first_message(transport=transport, paths=paths)
    _ingest(transport, paths)

    reply_to_request(
        transport=transport,
        paths=paths,
        update_id=TelegramUpdateId(11),
        message_id=TelegramMessageId(2),
        text="Second one done.",
    )

    assert [request.text for request in read_pending_requests(paths)] == ["first ask"]


def test_re_ingesting_the_same_update_does_not_queue_it_twice() -> None:
    """A crash between writing the queue and acknowledging Telegram re-delivers the update."""
    update = build_telegram_update(update_id=10, chat_id=int(OWNER_CHAT_ID), message_id=1, text="once")
    state = _build_state(acknowledged_offset=0, pending_requests=())

    first = ingest_updates(updates=[update], state=state, allowed_chat_id=OWNER_CHAT_ID)
    second = ingest_updates(updates=[update], state=first.state, allowed_chat_id=OWNER_CHAT_ID)

    assert len(second.state.pending_requests) == 1
    assert second.newly_queued_requests == ()


def test_the_offset_moves_past_updates_that_were_not_requests() -> None:
    updates = [
        build_telegram_update(update_id=10, chat_id=int(STRANGER_CHAT_ID), message_id=1, text="not for us"),
        build_telegram_update(update_id=11, chat_id=int(OWNER_CHAT_ID), message_id=1, text="mine"),
    ]
    state = _build_state(acknowledged_offset=0, pending_requests=())

    result = ingest_updates(updates=updates, state=state, allowed_chat_id=OWNER_CHAT_ID)

    assert int(result.state.acknowledged_offset) == 12


def test_an_empty_batch_leaves_the_offset_alone() -> None:
    state = _build_state(acknowledged_offset=7, pending_requests=())

    result = ingest_updates(updates=[], state=state, allowed_chat_id=OWNER_CHAT_ID)

    assert int(result.state.acknowledged_offset) == 7


def test_removing_a_request_keeps_the_offset() -> None:
    state = _build_state(
        acknowledged_offset=12,
        pending_requests=(_build_request(update_id=10, message_id=1, text="a"),),
    )

    trimmed = without_request(state=state, update_id=TelegramUpdateId(10))

    assert trimmed.pending_requests == ()
    assert int(trimmed.acknowledged_offset) == 12


def test_the_listener_wakes_the_agent_when_a_request_arrives(tmp_path: Path) -> None:
    transport = build_recording_telegram_transport(
        available_updates=[
            build_telegram_update(update_id=10, chat_id=int(OWNER_CHAT_ID), message_id=1, text="do a thing"),
        ],
    )
    paths = build_telegram_inbox_paths(root_directory=tmp_path)
    claim_chat_from_first_message(transport=transport, paths=paths)
    waker = RecordingAgentWaker()

    listen_forever(
        transport=transport,
        paths=paths,
        waker=waker,
        settings=ListenerSettings(
            inbox_root=tmp_path,
            long_poll_seconds=0,
            min_seconds_between_wakes=0.0,
        ),
        max_poll_count=1,
    )

    assert waker.wake_count == 1
    assert [request.text for request in read_pending_requests(paths)] == ["do a thing"]


def test_the_listener_stays_quiet_when_nothing_arrives(tmp_path: Path) -> None:
    transport = build_recording_telegram_transport(available_updates=[])
    paths = build_telegram_inbox_paths(root_directory=tmp_path)
    claim_chat_from_first_message(transport=transport, paths=paths)
    # Claiming with no messages leaves the inbox unclaimed, so seed it directly.
    write_configuration(paths=paths, chat_id=OWNER_CHAT_ID)
    write_state(paths=paths, state=_build_state(acknowledged_offset=0, pending_requests=()))
    waker = RecordingAgentWaker()

    listen_forever(
        transport=transport,
        paths=paths,
        waker=waker,
        settings=ListenerSettings(
            inbox_root=tmp_path,
            long_poll_seconds=0,
            min_seconds_between_wakes=0.0,
        ),
        max_poll_count=3,
    )

    assert waker.wake_count == 0


def test_the_listener_wakes_once_for_a_burst_of_messages(tmp_path: Path) -> None:
    transport = build_recording_telegram_transport(
        available_updates=[
            build_telegram_update(update_id=10, chat_id=int(OWNER_CHAT_ID), message_id=1, text="one"),
            build_telegram_update(update_id=11, chat_id=int(OWNER_CHAT_ID), message_id=2, text="two"),
            build_telegram_update(update_id=12, chat_id=int(OWNER_CHAT_ID), message_id=3, text="three"),
        ],
    )
    paths = build_telegram_inbox_paths(root_directory=tmp_path)
    claim_chat_from_first_message(transport=transport, paths=paths)
    waker = RecordingAgentWaker()

    listen_forever(
        transport=transport,
        paths=paths,
        waker=waker,
        settings=ListenerSettings(
            inbox_root=tmp_path,
            long_poll_seconds=0,
            min_seconds_between_wakes=0.0,
        ),
        max_poll_count=2,
    )

    assert waker.wake_count == 1
    assert len(read_pending_requests(paths)) == 3


def test_the_listener_leaves_the_request_queued_when_the_wake_fails(tmp_path: Path) -> None:
    """A failed wake must not consume the request: the scheduled check is the backstop."""
    transport = build_recording_telegram_transport(
        available_updates=[
            build_telegram_update(update_id=10, chat_id=int(OWNER_CHAT_ID), message_id=1, text="do a thing"),
        ],
    )
    paths = build_telegram_inbox_paths(root_directory=tmp_path)
    claim_chat_from_first_message(transport=transport, paths=paths)

    waker = FailingAgentWaker()
    listen_forever(
        transport=transport,
        paths=paths,
        waker=waker,
        settings=ListenerSettings(
            inbox_root=tmp_path,
            long_poll_seconds=0,
            min_seconds_between_wakes=0.0,
        ),
        max_poll_count=1,
    )

    assert waker.wake_count == 1
    assert [request.text for request in read_pending_requests(paths)] == ["do a thing"]


def test_an_unclaimed_inbox_is_reported_as_unconfigured(tmp_path: Path) -> None:
    with pytest.raises(TelegramNotConfiguredError):
        read_pending_requests(build_telegram_inbox_paths(root_directory=tmp_path))


def test_the_stored_state_round_trips(tmp_path: Path) -> None:
    paths = build_telegram_inbox_paths(root_directory=tmp_path)
    state = _build_state(
        acknowledged_offset=12,
        pending_requests=(_build_request(update_id=11, message_id=3, text="queued"),),
    )
    write_state(paths=paths, state=state)

    assert load_state(paths) == state
