import json
from pathlib import Path

import pytest
from mock_telegram_transport_test import build_recording_telegram_transport
from mock_telegram_transport_test import build_telegram_update
from telegram_inbox import claim_chat_from_first_message
from telegram_inbox import collect_pending_requests
from telegram_inbox import reply_to_request
from telegram_logic import compute_acknowledged_offset
from telegram_logic import prune_state_for_storage
from telegram_store import build_telegram_inbox_paths
from telegram_store import load_chat_id
from telegram_store import load_state
from telegram_types import TelegramChatId
from telegram_types import TelegramInboxState
from telegram_types import TelegramMessageId
from telegram_types import TelegramNotConfiguredError
from telegram_types import TelegramUpdateId

OWNER_CHAT_ID: TelegramChatId = TelegramChatId(11111)
STRANGER_CHAT_ID: TelegramChatId = TelegramChatId(99999)


def _build_state(acknowledged_offset: int, handled_update_ids: tuple[int, ...]) -> TelegramInboxState:
    return TelegramInboxState(
        acknowledged_offset=TelegramUpdateId(acknowledged_offset),
        handled_update_ids=tuple(TelegramUpdateId(value) for value in handled_update_ids),
    )


def test_claiming_adopts_the_chat_that_messaged_first(tmp_path: Path) -> None:
    transport = build_recording_telegram_transport(
        available_updates=[
            build_telegram_update(update_id=10, chat_id=int(OWNER_CHAT_ID), message_id=1, text="hello"),
        ],
    )
    paths = build_telegram_inbox_paths(root_directory=tmp_path)

    claim_result = claim_chat_from_first_message(transport=transport, paths=paths)

    assert claim_result.chat_id == OWNER_CHAT_ID
    assert load_chat_id(paths) == OWNER_CHAT_ID


def test_claiming_with_no_messages_yet_changes_nothing(tmp_path: Path) -> None:
    transport = build_recording_telegram_transport(available_updates=[])
    paths = build_telegram_inbox_paths(root_directory=tmp_path)

    assert claim_chat_from_first_message(transport=transport, paths=paths).chat_id is None
    with pytest.raises(TelegramNotConfiguredError):
        load_chat_id(paths)


def test_a_message_from_the_claimed_chat_becomes_a_request(tmp_path: Path) -> None:
    transport = build_recording_telegram_transport(
        available_updates=[
            build_telegram_update(update_id=10, chat_id=int(OWNER_CHAT_ID), message_id=1, text="hello"),
            build_telegram_update(update_id=11, chat_id=int(OWNER_CHAT_ID), message_id=2, text="book a table"),
        ],
    )
    paths = build_telegram_inbox_paths(root_directory=tmp_path)
    claim_chat_from_first_message(transport=transport, paths=paths)

    reports = collect_pending_requests(transport=transport, paths=paths)

    assert [report.request.text for report in reports] == ["hello", "book a table"]
    archived = json.loads(reports[0].raw_record_path.read_text())
    assert archived["telegram_update"]["message"]["text"] == "hello"


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

    reports = collect_pending_requests(transport=transport, paths=paths)

    assert [report.request.text for report in reports] == ["mine"]


def test_an_answered_request_stops_being_pending(tmp_path: Path) -> None:
    transport = build_recording_telegram_transport(
        available_updates=[
            build_telegram_update(update_id=10, chat_id=int(OWNER_CHAT_ID), message_id=1, text="what day is it"),
        ],
    )
    paths = build_telegram_inbox_paths(root_directory=tmp_path)
    claim_chat_from_first_message(transport=transport, paths=paths)
    collect_pending_requests(transport=transport, paths=paths)

    reply_to_request(
        transport=transport,
        paths=paths,
        update_id=TelegramUpdateId(10),
        message_id=TelegramMessageId(1),
        text="Tuesday.",
    )

    assert transport.sent_messages[0]["text"] == "Tuesday."
    assert transport.sent_messages[0]["reply_to_message_id"] == 1
    assert collect_pending_requests(transport=transport, paths=paths) == ()


def test_reading_twice_without_replying_keeps_the_request(tmp_path: Path) -> None:
    """Telegram drops acknowledged updates for good, so an unanswered one must never be acknowledged."""
    transport = build_recording_telegram_transport(
        available_updates=[
            build_telegram_update(update_id=10, chat_id=int(OWNER_CHAT_ID), message_id=1, text="still waiting"),
        ],
    )
    paths = build_telegram_inbox_paths(root_directory=tmp_path)
    claim_chat_from_first_message(transport=transport, paths=paths)

    first_reports = collect_pending_requests(transport=transport, paths=paths)
    second_reports = collect_pending_requests(transport=transport, paths=paths)

    assert [report.request.update_id for report in first_reports] == [
        report.request.update_id for report in second_reports
    ]


def test_an_older_unanswered_request_is_not_acknowledged_away(tmp_path: Path) -> None:
    transport = build_recording_telegram_transport(
        available_updates=[
            build_telegram_update(update_id=10, chat_id=int(OWNER_CHAT_ID), message_id=1, text="first ask"),
            build_telegram_update(update_id=11, chat_id=int(OWNER_CHAT_ID), message_id=2, text="second ask"),
        ],
    )
    paths = build_telegram_inbox_paths(root_directory=tmp_path)
    claim_chat_from_first_message(transport=transport, paths=paths)
    collect_pending_requests(transport=transport, paths=paths)

    reply_to_request(
        transport=transport,
        paths=paths,
        update_id=TelegramUpdateId(11),
        message_id=TelegramMessageId(2),
        text="Second one done.",
    )

    reports = collect_pending_requests(transport=transport, paths=paths)
    assert [report.request.text for report in reports] == ["first ask"]
    assert int(load_state(paths).acknowledged_offset) == 0


def test_the_offset_advances_over_a_fully_settled_run() -> None:
    updates = [
        build_telegram_update(update_id=10, chat_id=int(OWNER_CHAT_ID), message_id=1, text="answered"),
        build_telegram_update(update_id=11, chat_id=int(STRANGER_CHAT_ID), message_id=1, text="not for us"),
        build_telegram_update(update_id=12, chat_id=int(OWNER_CHAT_ID), message_id=2, text="waiting"),
    ]
    state = _build_state(acknowledged_offset=0, handled_update_ids=(10,))

    offset = compute_acknowledged_offset(updates=updates, state=state, allowed_chat_id=OWNER_CHAT_ID)

    assert int(offset) == 12


def test_pruning_forgets_updates_the_offset_has_passed() -> None:
    state = _build_state(acknowledged_offset=12, handled_update_ids=(10, 11, 12, 13))

    pruned = prune_state_for_storage(state=state, max_remembered_update_count=10)

    assert [int(value) for value in pruned.handled_update_ids] == [12, 13]
