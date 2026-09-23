import json
from pathlib import Path

import pytest
from inbox_store import build_inbox_paths
from inbox_store import load_state
from inbox_types import InboxNotConfiguredError
from inbox_types import SlackConversationId
from inbox_types import SlackTimestamp
from mock_slack_transport_test import build_recording_transport
from mock_slack_transport_test import build_user_message
from slack_inbox import collect_pending_requests
from slack_inbox import initialize_inbox
from slack_inbox import reply_to_request

CONVERSATION_ID: SlackConversationId = SlackConversationId("D_SELF")


def _initialize(root_directory: Path, starting_ts: str) -> None:
    initialize_inbox(
        paths=build_inbox_paths(root_directory=root_directory),
        conversation_id=CONVERSATION_ID,
        starting_ts=SlackTimestamp(starting_ts),
    )


def test_a_new_inbox_ignores_messages_already_in_the_conversation(tmp_path: Path) -> None:
    _initialize(root_directory=tmp_path, starting_ts="500.000000")
    transport = build_recording_transport(
        timeline_messages=[build_user_message(message_ts="400.000000", text="old chatter", thread_ts=None)],
        thread_messages_by_thread_ts={},
    )

    reports = collect_pending_requests(transport=transport, paths=build_inbox_paths(root_directory=tmp_path))

    assert reports == ()


def test_a_new_message_is_reported_and_archived_before_any_work(tmp_path: Path) -> None:
    _initialize(root_directory=tmp_path, starting_ts="500.000000")
    transport = build_recording_transport(
        timeline_messages=[build_user_message(message_ts="600.000000", text="check my calendar", thread_ts=None)],
        thread_messages_by_thread_ts={},
    )

    reports = collect_pending_requests(transport=transport, paths=build_inbox_paths(root_directory=tmp_path))

    assert len(reports) == 1
    assert reports[0].request.text == "check my calendar"
    archived_record = json.loads(reports[0].raw_record_path.read_text())
    assert archived_record["slack_message"]["text"] == "check my calendar"
    assert archived_record["permalink"].startswith("https://slack.test/")


def test_reading_the_queue_twice_reports_the_same_request(tmp_path: Path) -> None:
    """Reading must not consume: a run that dies after reading has to leave the work pending."""
    _initialize(root_directory=tmp_path, starting_ts="500.000000")
    transport = build_recording_transport(
        timeline_messages=[build_user_message(message_ts="600.000000", text="still waiting", thread_ts=None)],
        thread_messages_by_thread_ts={},
    )
    paths = build_inbox_paths(root_directory=tmp_path)

    first_reports = collect_pending_requests(transport=transport, paths=paths)
    second_reports = collect_pending_requests(transport=transport, paths=paths)

    assert [report.request.request_ts for report in first_reports] == [
        report.request.request_ts for report in second_reports
    ]


def test_a_replied_request_stops_being_pending(tmp_path: Path) -> None:
    _initialize(root_directory=tmp_path, starting_ts="500.000000")
    request = build_user_message(message_ts="600.000000", text="book me a table", thread_ts=None)
    transport = build_recording_transport(timeline_messages=[request], thread_messages_by_thread_ts={})
    paths = build_inbox_paths(root_directory=tmp_path)

    collect_pending_requests(transport=transport, paths=paths)
    posted_ts = reply_to_request(
        transport=transport,
        paths=paths,
        request_ts=SlackTimestamp("600.000000"),
        thread_ts=SlackTimestamp("600.000000"),
        text="Booked for 7pm.",
    )

    assert transport.posted_messages[0]["text"] == "Booked for 7pm."
    assert transport.posted_messages[0]["thread_ts"] == "600.000000"
    assert collect_pending_requests(transport=transport, paths=paths) == ()
    assert posted_ts in load_state(paths).posted_ts


def test_a_follow_up_inside_an_answered_thread_is_picked_up(tmp_path: Path) -> None:
    _initialize(root_directory=tmp_path, starting_ts="500.000000")
    request = build_user_message(message_ts="600.000000", text="book me a table", thread_ts=None)
    transport = build_recording_transport(timeline_messages=[request], thread_messages_by_thread_ts={})
    paths = build_inbox_paths(root_directory=tmp_path)
    collect_pending_requests(transport=transport, paths=paths)
    reply_to_request(
        transport=transport,
        paths=paths,
        request_ts=SlackTimestamp("600.000000"),
        thread_ts=SlackTimestamp("600.000000"),
        text="Booked for 7pm.",
    )

    # The user answers inside the thread rather than starting a new message.
    follow_up = build_user_message(message_ts="700.000000", text="make it 8pm", thread_ts="600.000000")
    transport.thread_messages_by_thread_ts["600.000000"] = (request, follow_up)

    reports = collect_pending_requests(transport=transport, paths=paths)

    assert [report.request.text for report in reports] == ["make it 8pm"]


def test_an_unanswered_older_request_survives_answering_a_newer_one(tmp_path: Path) -> None:
    _initialize(root_directory=tmp_path, starting_ts="500.000000")
    transport = build_recording_transport(
        timeline_messages=[
            build_user_message(message_ts="600.000000", text="first ask", thread_ts=None),
            build_user_message(message_ts="700.000000", text="second ask", thread_ts=None),
        ],
        thread_messages_by_thread_ts={},
    )
    paths = build_inbox_paths(root_directory=tmp_path)
    collect_pending_requests(transport=transport, paths=paths)

    reply_to_request(
        transport=transport,
        paths=paths,
        request_ts=SlackTimestamp("700.000000"),
        thread_ts=SlackTimestamp("700.000000"),
        text="Second one done.",
    )

    reports = collect_pending_requests(transport=transport, paths=paths)
    assert [report.request.text for report in reports] == ["first ask"]


def test_using_an_uninitialized_inbox_fails_loudly(tmp_path: Path) -> None:
    transport = build_recording_transport(timeline_messages=[], thread_messages_by_thread_ts={})

    with pytest.raises(InboxNotConfiguredError):
        collect_pending_requests(transport=transport, paths=build_inbox_paths(root_directory=tmp_path))
