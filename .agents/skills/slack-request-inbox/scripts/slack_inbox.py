import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any
from typing import Final

from imbue.imbue_common.frozen_model import FrozenModel
from imbue.imbue_common.model_update import to_update
from inbox_logic import compute_settled_watermark
from inbox_logic import prune_state_for_storage
from inbox_logic import select_pending_requests
from inbox_store import DEFAULT_INBOX_ROOT
from inbox_store import MAX_REMEMBERED_TIMESTAMP_COUNT
from inbox_store import InboxPaths
from inbox_store import build_inbox_paths
from inbox_store import load_conversation_id
from inbox_store import load_state
from inbox_store import persist_raw_request
from inbox_store import with_handled_request
from inbox_store import with_posted_message
from inbox_store import with_watched_thread
from inbox_store import with_watermark
from inbox_store import write_configuration
from inbox_store import write_state
from inbox_types import InboxRequest
from inbox_types import InboxState
from inbox_types import SlackConversationId
from inbox_types import SlackTimestamp
from loguru import logger
from pydantic import Field
from slack_transport import LatchkeySlackTransport
from slack_transport import SlackTransportInterface

LATCHKEY_EXECUTABLE: Final[str] = "latchkey"

# Only the most recent threads stay on the poll list; older ones would cost a Slack call each run forever.
MAX_WATCHED_THREAD_COUNT: Final[int] = 20


class PendingRequestReport(FrozenModel):
    """One pending request as the agent reads it, including where its raw record was archived."""

    request: InboxRequest = Field(description="The request itself")
    raw_record_path: Path = Field(description="Where the untouched Slack record was archived")


def collect_pending_requests(
    transport: SlackTransportInterface,
    paths: InboxPaths,
) -> tuple[PendingRequestReport, ...]:
    """Read the watched conversation, archive anything new, and return the unanswered requests."""
    conversation_id = load_conversation_id(paths)
    stored_state = load_state(paths)

    # Read the main timeline plus every thread already replied in, so a follow-up inside a
    # thread is picked up as readily as a fresh message.
    history = transport.read_conversation_history(
        conversation_id=conversation_id,
        oldest_ts=stored_state.oldest_ts,
    )
    thread_messages: list[dict[str, Any]] = []
    for thread_ts in stored_state.watched_thread_ts:
        thread_history = transport.read_thread_replies(conversation_id=conversation_id, thread_ts=thread_ts)
        thread_messages.extend(thread_history.messages)

    # Advance the watermark only over the leading run of settled timeline messages.
    advanced_state = with_watermark(
        state=stored_state,
        oldest_ts=compute_settled_watermark(messages=history.messages, state=stored_state),
    )

    # Archive every pending request's raw record before anything acts on it.
    pending_requests = select_pending_requests(
        messages=list(history.messages) + thread_messages,
        state=advanced_state,
    )
    reports: list[PendingRequestReport] = []
    for request in pending_requests:
        permalink = transport.read_permalink(conversation_id=conversation_id, message_ts=request.request_ts)
        request_with_permalink = request.model_copy_update(
            to_update(request.field_ref().permalink, permalink),
        )
        reports.append(
            PendingRequestReport(
                request=request_with_permalink,
                raw_record_path=persist_raw_request(paths=paths, request=request_with_permalink),
            )
        )

    write_state(
        paths=paths,
        state=prune_state_for_storage(
            state=advanced_state,
            max_remembered_timestamp_count=MAX_REMEMBERED_TIMESTAMP_COUNT,
            max_watched_thread_count=MAX_WATCHED_THREAD_COUNT,
        ),
    )
    return tuple(reports)


def reply_to_request(
    transport: SlackTransportInterface,
    paths: InboxPaths,
    request_ts: SlackTimestamp,
    thread_ts: SlackTimestamp,
    text: str,
) -> SlackTimestamp:
    """Post one reply into the request's thread and record the request as answered."""
    conversation_id = load_conversation_id(paths)
    stored_state = load_state(paths)
    posted_ts = transport.post_message(conversation_id=conversation_id, thread_ts=thread_ts, text=text)
    answered_state = with_watched_thread(
        state=with_posted_message(
            state=with_handled_request(state=stored_state, request_ts=request_ts),
            posted_ts=posted_ts,
        ),
        thread_ts=thread_ts,
    )
    write_state(
        paths=paths,
        state=prune_state_for_storage(
            state=answered_state,
            max_remembered_timestamp_count=MAX_REMEMBERED_TIMESTAMP_COUNT,
            max_watched_thread_count=MAX_WATCHED_THREAD_COUNT,
        ),
    )
    return posted_ts


def initialize_inbox(
    paths: InboxPaths,
    conversation_id: SlackConversationId,
    starting_ts: SlackTimestamp,
) -> None:
    """Point the inbox at a conversation and ignore everything already in it."""
    write_configuration(paths=paths, conversation_id=conversation_id)
    write_state(
        paths=paths,
        state=InboxState(oldest_ts=starting_ts, handled_ts=(), posted_ts=(), watched_thread_ts=()),
    )


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read and answer requests sent to a Slack conversation.")
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_INBOX_ROOT,
        help="Directory holding this inbox's configuration, state, and raw archive",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Point the inbox at a Slack conversation")
    init_parser.add_argument("--conversation-id", required=True, help="Slack channel or DM id to watch")

    pending_parser = subparsers.add_parser("pending", help="List requests that have not been answered")
    pending_parser.add_argument(
        "--count-only",
        action="store_true",
        help="Print only how many requests are waiting",
    )

    reply_parser = subparsers.add_parser("reply", help="Answer one request in its thread")
    reply_parser.add_argument("--request-ts", required=True, help="Timestamp of the request being answered")
    reply_parser.add_argument("--thread-ts", required=True, help="Timestamp of the thread to reply in")
    reply_parser.add_argument("--text", required=True, help="The reply text to post")
    return parser


def main() -> None:
    parser = _build_argument_parser()
    arguments = parser.parse_args()
    paths = build_inbox_paths(root_directory=arguments.root)
    transport = LatchkeySlackTransport(latchkey_executable=LATCHKEY_EXECUTABLE)

    if arguments.command == "init":
        initialize_inbox(
            paths=paths,
            conversation_id=SlackConversationId(arguments.conversation_id),
            starting_ts=SlackTimestamp(f"{time.time():.6f}"),
        )
        logger.info("Watching Slack conversation {} for requests", arguments.conversation_id)
    elif arguments.command == "pending":
        reports = collect_pending_requests(transport=transport, paths=paths)
        if arguments.count_only:
            print(len(reports))
        else:
            print(json.dumps([report.model_dump(mode="json") for report in reports], indent=2))
    elif arguments.command == "reply":
        posted_ts = reply_to_request(
            transport=transport,
            paths=paths,
            request_ts=SlackTimestamp(arguments.request_ts),
            thread_ts=SlackTimestamp(arguments.thread_ts),
            text=arguments.text,
        )
        logger.info("Replied in Slack (message {})", posted_ts)
    else:
        parser.error(f"Unknown command: {arguments.command}")
        sys.exit(2)


if __name__ == "__main__":
    main()
