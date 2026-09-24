from collections.abc import Sequence
from typing import Any
from typing import Final

from imbue.imbue_common.pure import pure
from telegram_types import TelegramChatId
from telegram_types import TelegramInboxState
from telegram_types import TelegramMessageId
from telegram_types import TelegramRequest
from telegram_types import TelegramUpdateId

# Commands the Telegram client itself sends or offers, rather than something the user meant as a
# request: opening a bot for the first time sends /start on its own. Answering these would spend an
# agent run on a button press. Any other slash-prefixed text is treated as a normal request.
TELEGRAM_CLIENT_COMMANDS: Final[frozenset[str]] = frozenset({"/start", "/help"})


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
def select_pending_requests(
    updates: Sequence[dict[str, Any]],
    state: TelegramInboxState,
    allowed_chat_id: TelegramChatId,
) -> tuple[TelegramRequest, ...]:
    """Pick out the updates that are unanswered requests, oldest first."""
    handled_ids = frozenset(int(update_id) for update_id in state.handled_update_ids)
    pending_requests: list[TelegramRequest] = []
    for update in sorted(updates, key=lambda candidate: int(candidate["update_id"])):
        if int(update["update_id"]) in handled_ids:
            continue
        request = extract_request(update=update, allowed_chat_id=allowed_chat_id)
        if request is not None:
            pending_requests.append(request)
    return tuple(pending_requests)


@pure
def compute_acknowledged_offset(
    updates: Sequence[dict[str, Any]],
    state: TelegramInboxState,
    allowed_chat_id: TelegramChatId,
) -> TelegramUpdateId:
    """Advance the offset over the leading run of settled updates, stopping at the first pending one.

    Telegram discards everything below the offset it is next asked for, so the offset must never
    move past an update that has not been answered -- that update would be gone for good.
    """
    handled_ids = frozenset(int(update_id) for update_id in state.handled_update_ids)
    acknowledged_offset = int(state.acknowledged_offset)
    for update in sorted(updates, key=lambda candidate: int(candidate["update_id"])):
        update_id = int(update["update_id"])
        if update_id < acknowledged_offset:
            continue
        is_settled = update_id in handled_ids or extract_request(update=update, allowed_chat_id=allowed_chat_id) is None
        if not is_settled:
            break
        acknowledged_offset = update_id + 1
    return TelegramUpdateId(acknowledged_offset)


@pure
def prune_state_for_storage(
    state: TelegramInboxState,
    max_remembered_update_count: int,
) -> TelegramInboxState:
    """Forget answered updates the offset has already left behind, keeping the newest of the rest."""
    retained = sorted(
        (update_id for update_id in state.handled_update_ids if int(update_id) >= int(state.acknowledged_offset)),
        key=int,
    )
    return TelegramInboxState(
        acknowledged_offset=state.acknowledged_offset,
        handled_update_ids=tuple(retained[-max_remembered_update_count:]),
    )
