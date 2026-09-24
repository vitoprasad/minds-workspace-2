"""A chat's routing state: whether it picks its own model, and what it has learned doing so.

Kept in the chat's own folder beside its fast mode (``chat_fast_mode.py``), for the same reason:
the choice belongs to the chat, so it travels with the chat across handoffs and survives reloads.

Two things are remembered rather than recomputed. ``tier`` is the difficulty the chat last settled
on, so a bare "keep going" inherits the reasoning the work already needed instead of reading as a
trivial request. ``exhausted_accounts`` are the accounts that stopped answering for this chat --
spent credits, a rate limit, a rejected credential -- so the chat does not walk back into one it
just failed on. An account is forgiven once the user acts on it (signing in again, or turning
routing off and on), never on a timer: a credit balance does not refill because a minute passed.
"""

import json
from pathlib import Path
from typing import Final

from loguru import logger as _loguru_logger
from pydantic import Field
from pydantic import ValidationError

from imbue.chat.chat_settings import RoutingMode
from imbue.chat.routing_policy import RoutingTier
from imbue.imbue_common.frozen_model import FrozenModel

logger = _loguru_logger

ROUTING_FILENAME: Final[str] = "routing.json"


class ChatRoutingState(FrozenModel):
    """Whether a chat routes itself, the difficulty it last settled on, and the accounts that failed it."""

    mode: RoutingMode = Field(description="off or auto")
    tier: RoutingTier | None = Field(
        default=None,
        description="The difficulty the chat last settled on; a continuation inherits it",
    )
    exhausted_accounts: tuple[str, ...] = Field(
        default=(),
        description="Accounts that stopped answering this chat, which routing will not choose again",
    )

    @property
    def is_routed(self) -> bool:
        """Whether this chat should weigh its next turn and move itself."""
        return self.mode is RoutingMode.AUTO


def read_routing_state(chat_dir: Path) -> ChatRoutingState | None:
    """The chat's routing state as written; None for a chat that has none yet, or an unreadable file (warned about)."""
    path = chat_dir / ROUTING_FILENAME
    if not path.is_file():
        return None
    try:
        return ChatRoutingState.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, ValidationError) as e:
        logger.warning("Ignoring an unreadable routing file at {}: {}", path, e)
        return None


def write_routing_state(chat_dir: Path, state: ChatRoutingState) -> None:
    """Write the chat's routing state whole (a temp file renamed into place)."""
    chat_dir.mkdir(parents=True, exist_ok=True)
    path = chat_dir / ROUTING_FILENAME
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(state.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp_path.replace(path)
