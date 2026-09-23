import argparse
import json
from pathlib import Path
from typing import Final

from imbue.imbue_common.frozen_model import FrozenModel
from inbox_store import DEFAULT_INBOX_ROOT
from loguru import logger
from pydantic import Field
from telegram_logic import compute_acknowledged_offset
from telegram_logic import prune_state_for_storage
from telegram_logic import select_pending_requests
from telegram_store import MAX_REMEMBERED_UPDATE_COUNT
from telegram_store import TelegramInboxPaths
from telegram_store import build_telegram_inbox_paths
from telegram_store import load_chat_id
from telegram_store import load_state
from telegram_store import persist_raw_request
from telegram_store import with_acknowledged_offset
from telegram_store import with_handled_update
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


class TelegramPendingRequestReport(FrozenModel):
    """One pending Telegram request, with where its raw record was archived."""

    request: TelegramRequest = Field(description="The request itself")
    raw_record_path: Path = Field(description="Where the untouched Telegram record was archived")


class ChatClaimResult(FrozenModel):
    """What claiming a chat found: the chat now watched, or nothing yet."""

    chat_id: TelegramChatId | None = Field(description="The chat that was claimed, if any message had arrived")


def claim_chat_from_first_message(
    transport: TelegramTransportInterface,
    paths: TelegramInboxPaths,
) -> ChatClaimResult:
    """Adopt the chat of the first message sent to the bot, and accept requests only from it."""
    batch = transport.read_updates(offset=TelegramUpdateId(FIRST_UPDATE_OFFSET))
    for update in batch.updates:
        message = update.get("message")
        if isinstance(message, dict) and isinstance(message.get("chat"), dict):
            chat_id = TelegramChatId(message["chat"]["id"])
            write_configuration(paths=paths, chat_id=chat_id)
            write_state(
                paths=paths,
                state=TelegramInboxState(
                    acknowledged_offset=TelegramUpdateId(FIRST_UPDATE_OFFSET),
                    handled_update_ids=(),
                ),
            )
            return ChatClaimResult(chat_id=chat_id)
    return ChatClaimResult(chat_id=None)


def collect_pending_requests(
    transport: TelegramTransportInterface,
    paths: TelegramInboxPaths,
) -> tuple[TelegramPendingRequestReport, ...]:
    """Read everything waiting on the bot, archive it, and return the unanswered requests."""
    allowed_chat_id = load_chat_id(paths)
    stored_state = load_state(paths)
    batch = transport.read_updates(offset=stored_state.acknowledged_offset)

    # Confirm only the settled run, so an unanswered update is never dropped by Telegram.
    advanced_offset = compute_acknowledged_offset(
        updates=batch.updates,
        state=stored_state,
        allowed_chat_id=allowed_chat_id,
    )
    advanced_state = with_acknowledged_offset(state=stored_state, acknowledged_offset=advanced_offset)
    if int(advanced_offset) > int(stored_state.acknowledged_offset):
        transport.acknowledge_updates_below(offset=advanced_offset)

    pending_requests = select_pending_requests(
        updates=batch.updates,
        state=advanced_state,
        allowed_chat_id=allowed_chat_id,
    )
    reports = tuple(
        TelegramPendingRequestReport(
            request=request,
            raw_record_path=persist_raw_request(paths=paths, request=request),
        )
        for request in pending_requests
    )
    write_state(
        paths=paths,
        state=prune_state_for_storage(
            state=advanced_state,
            max_remembered_update_count=MAX_REMEMBERED_UPDATE_COUNT,
        ),
    )
    return reports


def reply_to_request(
    transport: TelegramTransportInterface,
    paths: TelegramInboxPaths,
    update_id: TelegramUpdateId,
    message_id: TelegramMessageId,
    text: str,
) -> TelegramMessageId:
    """Answer one Telegram request and record it as answered."""
    allowed_chat_id = load_chat_id(paths)
    stored_state = load_state(paths)
    posted_message_id = transport.send_message(
        chat_id=allowed_chat_id,
        reply_to_message_id=message_id,
        text=text,
    )
    write_state(
        paths=paths,
        state=prune_state_for_storage(
            state=with_handled_update(state=stored_state, update_id=update_id),
            max_remembered_update_count=MAX_REMEMBERED_UPDATE_COUNT,
        ),
    )
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

    pending_parser = subparsers.add_parser("pending", help="List requests that have not been answered")
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
        claim_result = claim_chat_from_first_message(transport=transport, paths=paths)
        if claim_result.chat_id is None:
            logger.info("No Telegram message has arrived yet; send the bot a message and run this again")
        else:
            logger.info("Watching Telegram chat {} for requests", int(claim_result.chat_id))
    elif arguments.command == "pending":
        try:
            reports = collect_pending_requests(transport=transport, paths=paths)
        except TelegramNotConfiguredError:
            # An unclaimed Telegram inbox is a normal state, not a failure: the scheduled check
            # runs whether or not the user has set Telegram up.
            reports = ()
        if arguments.count_only:
            print(len(reports))
        else:
            print(json.dumps([report.model_dump(mode="json") for report in reports], indent=2))
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
