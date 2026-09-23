from collections.abc import Sequence
from typing import Any

from imbue.imbue_common.pure import pure
from inbox_types import InboxRequest
from inbox_types import InboxState
from inbox_types import SlackTimestamp
from inbox_types import SlackUserId

UNKNOWN_AUTHOR_USER_ID: SlackUserId = SlackUserId("unknown")


@pure
def is_timestamp_newer(candidate_ts: SlackTimestamp, reference_ts: SlackTimestamp) -> bool:
    return float(candidate_ts) > float(reference_ts)


@pure
def select_pending_requests(
    messages: Sequence[dict[str, Any]],
    state: InboxState,
) -> tuple[InboxRequest, ...]:
    """Pick out the messages that are genuine unanswered requests, oldest first."""
    settled_timestamps = frozenset(state.handled_ts) | frozenset(state.posted_ts)
    pending_requests: list[InboxRequest] = []
    for message in messages:
        message_ts = SlackTimestamp(message["ts"])
        if not is_timestamp_newer(candidate_ts=message_ts, reference_ts=state.oldest_ts):
            continue
        if message_ts in settled_timestamps:
            continue
        text = message.get("text", "")
        if not isinstance(text, str) or not text.strip():
            continue
        raw_thread_ts = message.get("thread_ts")
        author_user_id = message.get("user")
        pending_requests.append(
            InboxRequest(
                request_ts=message_ts,
                thread_ts=SlackTimestamp(raw_thread_ts) if isinstance(raw_thread_ts, str) else message_ts,
                author_user_id=SlackUserId(author_user_id)
                if isinstance(author_user_id, str) and author_user_id
                else UNKNOWN_AUTHOR_USER_ID,
                text=text,
                permalink=None,
                raw_message=dict(message),
            )
        )
    return tuple(sorted(pending_requests, key=lambda request: float(request.request_ts)))


@pure
def compute_settled_watermark(
    messages: Sequence[dict[str, Any]],
    state: InboxState,
) -> SlackTimestamp:
    """Advance the watermark over the leading run of settled messages, stopping at the first unsettled one.

    Stopping at the first unsettled message is what makes a crashed run safe: anything not yet
    answered stays in front of the watermark and is offered again on the next run.
    """
    settled_timestamps = frozenset(state.handled_ts) | frozenset(state.posted_ts)
    watermark_ts = state.oldest_ts
    for message in sorted(messages, key=lambda candidate: float(candidate["ts"])):
        message_ts = SlackTimestamp(message["ts"])
        if not is_timestamp_newer(candidate_ts=message_ts, reference_ts=watermark_ts):
            continue
        if message_ts not in settled_timestamps:
            break
        watermark_ts = message_ts
    return watermark_ts


@pure
def prune_state_for_storage(
    state: InboxState,
    max_remembered_timestamp_count: int,
    max_watched_thread_count: int,
) -> InboxState:
    """Keep the remembered timestamps bounded, always keeping the newest ones.

    A timestamp at or before the watermark can be forgotten: the watermark alone already
    settles it, because it is never offered as pending again.
    """
    retained_handled = tuple(
        message_ts
        for message_ts in state.handled_ts
        if is_timestamp_newer(candidate_ts=message_ts, reference_ts=state.oldest_ts)
    )
    retained_posted = tuple(
        message_ts
        for message_ts in state.posted_ts
        if is_timestamp_newer(candidate_ts=message_ts, reference_ts=state.oldest_ts)
    )
    return InboxState(
        oldest_ts=state.oldest_ts,
        handled_ts=_keep_newest(timestamps=retained_handled, max_count=max_remembered_timestamp_count),
        posted_ts=_keep_newest(timestamps=retained_posted, max_count=max_remembered_timestamp_count),
        watched_thread_ts=_keep_newest(
            timestamps=state.watched_thread_ts,
            max_count=max_watched_thread_count,
        ),
    )


@pure
def _keep_newest(timestamps: Sequence[SlackTimestamp], max_count: int) -> tuple[SlackTimestamp, ...]:
    ordered = sorted(timestamps, key=float)
    return tuple(ordered[-max_count:])
