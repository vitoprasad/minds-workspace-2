from inbox_logic import compute_settled_watermark
from inbox_logic import prune_state_for_storage
from inbox_logic import select_pending_requests
from inbox_types import InboxState
from inbox_types import SlackTimestamp
from mock_slack_transport_test import build_user_message


def _build_state(
    oldest_ts: str,
    handled_ts: tuple[str, ...],
    posted_ts: tuple[str, ...],
    watched_thread_ts: tuple[str, ...],
) -> InboxState:
    return InboxState(
        oldest_ts=SlackTimestamp(oldest_ts),
        handled_ts=tuple(SlackTimestamp(value) for value in handled_ts),
        posted_ts=tuple(SlackTimestamp(value) for value in posted_ts),
        watched_thread_ts=tuple(SlackTimestamp(value) for value in watched_thread_ts),
    )


def test_pending_skips_messages_at_or_before_the_watermark() -> None:
    messages = [
        build_user_message(message_ts="100.000000", text="old", thread_ts=None),
        build_user_message(message_ts="200.000000", text="new", thread_ts=None),
    ]
    state = _build_state(oldest_ts="100.000000", handled_ts=(), posted_ts=(), watched_thread_ts=())

    pending = select_pending_requests(messages=messages, state=state)

    assert [str(request.request_ts) for request in pending] == ["200.000000"]


def test_pending_excludes_the_inboxs_own_posts() -> None:
    """The Slack credential is the user's own account, so a reply looks exactly like a request."""
    messages = [
        build_user_message(message_ts="200.000000", text="do a thing", thread_ts=None),
        build_user_message(message_ts="201.000000", text="done", thread_ts="200.000000"),
    ]
    state = _build_state(
        oldest_ts="100.000000",
        handled_ts=("200.000000",),
        posted_ts=("201.000000",),
        watched_thread_ts=("200.000000",),
    )

    pending = select_pending_requests(messages=messages, state=state)

    assert pending == ()


def test_pending_treats_a_thread_follow_up_as_a_new_request() -> None:
    messages = [
        build_user_message(message_ts="200.000000", text="do a thing", thread_ts=None),
        build_user_message(message_ts="201.000000", text="done", thread_ts="200.000000"),
        build_user_message(message_ts="202.000000", text="and one more", thread_ts="200.000000"),
    ]
    state = _build_state(
        oldest_ts="100.000000",
        handled_ts=("200.000000",),
        posted_ts=("201.000000",),
        watched_thread_ts=("200.000000",),
    )

    pending = select_pending_requests(messages=messages, state=state)

    assert len(pending) == 1
    assert str(pending[0].request_ts) == "202.000000"
    assert str(pending[0].thread_ts) == "200.000000"


def test_pending_ignores_messages_with_no_text() -> None:
    messages = [
        {"ts": "200.000000", "text": "   ", "user": "U_USER"},
        {"ts": "201.000000", "user": "U_USER"},
    ]
    state = _build_state(oldest_ts="100.000000", handled_ts=(), posted_ts=(), watched_thread_ts=())

    assert select_pending_requests(messages=messages, state=state) == ()


def test_pending_is_ordered_oldest_first() -> None:
    messages = [
        build_user_message(message_ts="203.000000", text="third", thread_ts=None),
        build_user_message(message_ts="201.000000", text="first", thread_ts=None),
        build_user_message(message_ts="202.000000", text="second", thread_ts=None),
    ]
    state = _build_state(oldest_ts="100.000000", handled_ts=(), posted_ts=(), watched_thread_ts=())

    pending = select_pending_requests(messages=messages, state=state)

    assert [request.text for request in pending] == ["first", "second", "third"]


def test_watermark_stops_at_the_first_unanswered_request() -> None:
    """An unanswered request must stay in front of the watermark, or a crashed run would lose it."""
    messages = [
        build_user_message(message_ts="201.000000", text="answered", thread_ts=None),
        build_user_message(message_ts="202.000000", text="still waiting", thread_ts=None),
        build_user_message(message_ts="203.000000", text="answered later", thread_ts=None),
    ]
    state = _build_state(
        oldest_ts="200.000000",
        handled_ts=("201.000000", "203.000000"),
        posted_ts=(),
        watched_thread_ts=(),
    )

    watermark = compute_settled_watermark(messages=messages, state=state)

    assert str(watermark) == "201.000000"


def test_watermark_advances_over_a_fully_settled_run() -> None:
    messages = [
        build_user_message(message_ts="201.000000", text="answered", thread_ts=None),
        build_user_message(message_ts="202.000000", text="our own reply", thread_ts=None),
    ]
    state = _build_state(
        oldest_ts="200.000000",
        handled_ts=("201.000000",),
        posted_ts=("202.000000",),
        watched_thread_ts=(),
    )

    assert str(compute_settled_watermark(messages=messages, state=state)) == "202.000000"


def test_watermark_never_moves_backwards() -> None:
    messages = [build_user_message(message_ts="150.000000", text="old", thread_ts=None)]
    state = _build_state(oldest_ts="200.000000", handled_ts=(), posted_ts=(), watched_thread_ts=())

    assert str(compute_settled_watermark(messages=messages, state=state)) == "200.000000"


def test_pruning_keeps_the_newest_entries_and_forgets_settled_ones() -> None:
    state = _build_state(
        oldest_ts="300.000000",
        handled_ts=("100.000000", "301.000000", "302.000000"),
        posted_ts=("200.000000", "303.000000"),
        watched_thread_ts=("301.000000", "302.000000", "303.000000"),
    )

    pruned = prune_state_for_storage(
        state=state,
        max_remembered_timestamp_count=1,
        max_watched_thread_count=2,
    )

    assert [str(value) for value in pruned.handled_ts] == ["302.000000"]
    assert [str(value) for value in pruned.posted_ts] == ["303.000000"]
    assert [str(value) for value in pruned.watched_thread_ts] == ["302.000000", "303.000000"]
    assert str(pruned.oldest_ts) == "300.000000"
