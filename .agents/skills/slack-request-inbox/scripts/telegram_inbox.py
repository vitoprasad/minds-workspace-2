import argparse
import json
from pathlib import Path
from typing import Final

from imbue.imbue_common.frozen_model import FrozenModel
from inbox_store import DEFAULT_INBOX_ROOT
from loguru import logger
from pydantic import Field
from telegram_logic import ingest_updates
from telegram_logic import without_request
from telegram_store import TelegramInboxPaths
from telegram_store import build_telegram_inbox_paths
from telegram_store import load_chat_id
from telegram_store import load_state
from telegram_store import persist_raw_request
from telegram_store import write_configuration
from telegram_store import write_state
from telegram_transport import LatchkeyTelegramTransport
from telegram_transport import TelegramTransportInterface
from telegram_types import TelegramChatId
from telegram_types import TelegramInboxState
from telegram_types import TelegramMessageId
from telegram_types import TelegramNotConfiguredError
from telegram_types import TelegramRequest
from telegram_types import TelegramUpdateId

LATCHKEY_EXECUTABLE: Final[str] = "latchkey"

FIRST_UPDATE_OFFSET: Final[int] = 0

# Long-poll seconds for the listener: Telegram holds the connection open this long waiting for a
# message, so a message sent mid-poll comes back immediately rather than on the next tick.
LISTENER_LONG_POLL_SECONDS: Final[int] = 25

# Short-poll for one-shot ingests (the scheduled safety net), which must not block a cron tick.
ONE_SHOT_POLL_SECONDS: Final[int] = 0


class IngestReport(FrozenModel):
    """What one ingest took off Telegram."""

    newly_queued_requests: tuple[TelegramRequest, ...] = Field(description="Requests added to the queue")
    pending_request_count: int = Field(description="How many requests are waiting after the ingest")


def claim_chat_from_first_message(
    transport: TelegramTransportInterface,
    paths: TelegramInboxPaths,
) -> TelegramChatId | None:
    """Adopt the chat of the first message sent to the bot, and accept requests only from it."""
    batch = transport.read_updates(
        offset=TelegramUpdateId(FIRST_UPDATE_OFFSET),
        long_poll_seconds=ONE_SHOT_POLL_SECONDS,
    )
    for update in batch.updates:
        message = update.get("message")
        if isinstance(message, dict) and isinstance(message.get("chat"), dict):
            chat_id = TelegramChatId(message["chat"]["id"])
            write_configuration(paths=paths, chat_id=chat_id)
            write_state(
                paths=paths,
                state=TelegramInboxState(
                    acknowledged_offset=TelegramUpdateId(FIRST_UPDATE_OFFSET),
                    pending_requests=(),
                ),
            )
            return chat_id
    return None


def ingest_once(
    transport: TelegramTransportInterface,
    paths: TelegramInboxPaths,
    long_poll_seconds: int,
) -> IngestReport:
    """Take whatever Telegram is holding, write it into the durable queue, then acknowledge it.

    The order matters and is the whole point: Telegram gives each update to one reader and forgets
    it once acknowledged, so the queue must be on disk before the acknowledgement goes out.
    """
    allowed_chat_id = load_chat_id(paths)
    stored_state = load_state(paths)
    batch = transport.read_updates(
        offset=stored_state.acknowledged_offset,
        long_poll_seconds=long_poll_seconds,
    )
    ingest_result = ingest_updates(
        updates=batch.updates,
        state=stored_state,
        allowed_chat_id=allowed_chat_id,
    )
    for request in ingest_result.newly_queued_requests:
        persist_raw_request(paths=paths, request=request)
    if int(ingest_result.state.acknowledged_offset) != int(stored_state.acknowledged_offset):
        write_state(paths=paths, state=ingest_result.state)
        transport.acknowledge_updates_below(offset=ingest_result.state.acknowledged_offset)
    return IngestReport(
        newly_queued_requests=ingest_result.newly_queued_requests,
        pending_request_count=len(ingest_result.state.pending_requests),
    )


def read_pending_requests(paths: TelegramInboxPaths) -> tuple[TelegramRequest, ...]:
    """Read the durable queue. No network: ingest is what talks to Telegram."""
    return load_state(paths).pending_requests


def reply_to_request(
    transport: TelegramTransportInterface,
    paths: TelegramInboxPaths,
    update_id: TelegramUpdateId,
    message_id: TelegramMessageId,
    text: str,
) -> TelegramMessageId:
    """Answer one queued request and take it off the queue."""
    allowed_chat_id = load_chat_id(paths)
    stored_state = load_state(paths)
    posted_message_id = transport.send_message(
        chat_id=allowed_chat_id,
        reply_to_message_id=message_id,
        text=text,
    )
    write_state(paths=paths, state=without_request(state=stored_state, update_id=update_id))
    return posted_message_id


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read and answer requests sent to the user's Telegram bot.")
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_INBOX_ROOT,
        help="Directory holding this inbox's configuration, state, and raw archive",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("claim", help="Adopt the chat of the first message sent to the bot")

    ingest_parser = subparsers.add_parser("ingest", help="Take waiting messages off Telegram into the queue")
    ingest_parser.add_argument(
        "--long-poll-seconds",
        type=int,
        default=ONE_SHOT_POLL_SECONDS,
        help="Seconds to hold the connection open waiting for a message",
    )

    pending_parser = subparsers.add_parser("pending", help="List queued requests without touching Telegram")
    pending_parser.add_argument(
        "--count-only",
        action="store_true",
        help="Print only how many requests are waiting",
    )

    reply_parser = subparsers.add_parser("reply", help="Answer one request")
    reply_parser.add_argument("--update-id", required=True, type=int, help="Update id of the request")
    reply_parser.add_argument("--message-id", required=True, type=int, help="Message to reply to")
    reply_parser.add_argument("--text", required=True, help="The reply text to send")
    return parser


def main() -> None:
    parser = _build_argument_parser()
    arguments = parser.parse_args()
    paths = build_telegram_inbox_paths(root_directory=arguments.root)
    transport = LatchkeyTelegramTransport(latchkey_executable=LATCHKEY_EXECUTABLE)

    if arguments.command == "claim":
        claimed_chat_id = claim_chat_from_first_message(transport=transport, paths=paths)
        if claimed_chat_id is None:
            logger.info("No Telegram message has arrived yet; send the bot a message and run this again")
        else:
            logger.info("Watching Telegram chat {} for requests", int(claimed_chat_id))
    elif arguments.command == "ingest":
        report = ingest_once(
            transport=transport,
            paths=paths,
            long_poll_seconds=arguments.long_poll_seconds,
        )
        print(len(report.newly_queued_requests))
    elif arguments.command == "pending":
        try:
            pending_requests = read_pending_requests(paths)
        except TelegramNotConfiguredError:
            # An unclaimed Telegram inbox is a normal state, not a failure: the scheduled check
            # runs whether or not the user has set Telegram up.
            pending_requests = ()
        if arguments.count_only:
            print(len(pending_requests))
        else:
            print(json.dumps([request.model_dump(mode="json") for request in pending_requests], indent=2))
    elif arguments.command == "reply":
        posted_message_id = reply_to_request(
            transport=transport,
            paths=paths,
            update_id=TelegramUpdateId(arguments.update_id),
            message_id=TelegramMessageId(arguments.message_id),
            text=arguments.text,
        )
        logger.info("Replied on Telegram (message {})", int(posted_message_id))
    else:
        parser.error(f"Unknown command: {arguments.command}")


if __name__ == "__main__":
    main()
