from collections.abc import Sequence
from typing import Any
from typing import Final

from imbue.imbue_common.frozen_model import FrozenModel
from imbue.imbue_common.pure import pure
from pydantic import Field
from telegram_types import TelegramChatId
from telegram_types import TelegramInboxState
from telegram_types import TelegramMessageId
from telegram_types import TelegramRequest
from telegram_types import TelegramUpdateId

# Commands the Telegram client itself sends or offers, rather than something the user meant as a
# request: opening a bot for the first time sends /start on its own. Answering these would spend an
# agent run on a button press. Any other slash-prefixed text is treated as a normal request.
TELEGRAM_CLIENT_COMMANDS: Final[frozenset[str]] = frozenset({"/start", "/help"})


class IngestResult(FrozenModel):
    """The state after taking a batch of updates off Telegram, and what was newly queued."""

    state: TelegramInboxState = Field(description="State to persist before acknowledging Telegram")
    newly_queued_requests: tuple[TelegramRequest, ...] = Field(description="Requests this batch added")


@pure
def extract_request(update: Any, allowed_chat_id: TelegramChatId) -> TelegramRequest | None:
    """Turn one Telegram update into a request, or return None when it is not one to act on.

    Anything from a chat other than the claimed one is deliberately dropped: the bot's address is
    guessable, so only the chat the user claimed is ever treated as a source of instructions.
    """
    if not isinstance(update, dict):
        return None
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat")
    if not isinstance(chat, dict) or chat.get("id") != int(allowed_chat_id):
        return None
    text = message.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    if text.strip().lower() in TELEGRAM_CLIENT_COMMANDS:
        return None
    message_id = message.get("message_id")
    if not isinstance(message_id, int):
        return None
    return TelegramRequest(
        update_id=TelegramUpdateId(update["update_id"]),
        chat_id=allowed_chat_id,
        message_id=TelegramMessageId(message_id),
        text=text,
        raw_update=dict(update),
    )


@pure
def ingest_updates(
    updates: Sequence[dict[str, Any]],
    state: TelegramInboxState,
    allowed_chat_id: TelegramChatId,
) -> IngestResult:
    """Fold a batch of Telegram updates into the durable queue.

    The offset advances over the whole batch, because every request in it is now held here.
    Re-ingesting the same update is harmless: a request already queued is not queued twice, which
    is what makes a crash between writing the queue and acknowledging Telegram safe.
    """
    already_queued_ids = frozenset(int(request.update_id) for request in state.pending_requests)
    newly_queued: list[TelegramRequest] = []
    highest_update_id = int(state.acknowledged_offset) - 1
    for update in sorted(updates, key=lambda candidate: int(candidate["update_id"])):
        highest_update_id = max(highest_update_id, int(update["update_id"]))
        request = extract_request(update=update, allowed_chat_id=allowed_chat_id)
        if request is None or int(request.update_id) in already_queued_ids:
            continue
        newly_queued.append(request)
    return IngestResult(
        state=TelegramInboxState(
            acknowledged_offset=TelegramUpdateId(highest_update_id + 1),
            pending_requests=state.pending_requests + tuple(newly_queued),
        ),
        newly_queued_requests=tuple(newly_queued),
    )


@pure
def without_request(state: TelegramInboxState, update_id: TelegramUpdateId) -> TelegramInboxState:
    """Drop one request from the queue. Removal is the record that it was answered."""
    return TelegramInboxState(
        acknowledged_offset=state.acknowledged_offset,
        pending_requests=tuple(
            request for request in state.pending_requests if int(request.update_id) != int(update_id)
        ),
    )
