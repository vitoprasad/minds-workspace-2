from typing import Any

from imbue.imbue_common.frozen_model import FrozenModel
from imbue.imbue_common.primitives import NonNegativeInt
from pydantic import Field
from pydantic import GetCoreSchemaHandler
from pydantic_core import CoreSchema
from pydantic_core import core_schema


class TelegramInboxError(Exception):
    """Base exception for the Telegram side of the request inbox."""

    ...


class TelegramApiError(TelegramInboxError, OSError):
    """Raised when the Telegram API refuses a call or returns an unusable body."""

    ...


class TelegramStateError(TelegramInboxError, OSError):
    """Raised when the Telegram inbox's own stored state cannot be read or written."""

    ...


class TelegramNotConfiguredError(TelegramInboxError, OSError):
    """Raised when the Telegram inbox is used before a chat has been claimed."""

    ...


class TelegramUpdateId(NonNegativeInt):
    """Telegram's per-bot update sequence number."""

    ...


class TelegramChatId(int):
    """The id of a Telegram chat. Negative for groups, positive for one-to-one chats."""

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        source_type: Any,
        handler: GetCoreSchemaHandler,
    ) -> CoreSchema:
        return core_schema.no_info_after_validator_function(cls, core_schema.int_schema())


class TelegramMessageId(NonNegativeInt):
    """The id of one message within a Telegram chat."""

    ...


class TelegramRequest(FrozenModel):
    """One Telegram message waiting to be acted on."""

    update_id: TelegramUpdateId = Field(description="Telegram's sequence number for this update")
    chat_id: TelegramChatId = Field(description="Chat the message arrived in")
    message_id: TelegramMessageId = Field(description="Message to reply to")
    text: str = Field(description="The message text exactly as Telegram returned it")
    raw_update: dict[str, Any] = Field(description="The unmodified Telegram update record")


class TelegramInboxState(FrozenModel):
    """What the Telegram inbox remembers between runs."""

    # Telegram drops every update below the offset it is next asked for, so this only ever
    # advances past updates that have actually been answered.
    acknowledged_offset: TelegramUpdateId = Field(description="Updates below this are settled and gone")
    handled_update_ids: tuple[TelegramUpdateId, ...] = Field(description="Updates already answered")
