import json
import subprocess
import time
import urllib.parse
from abc import ABC
from abc import abstractmethod
from collections.abc import Mapping
from typing import Any
from typing import Final

from imbue.imbue_common.frozen_model import FrozenModel
from imbue.imbue_common.mutable_model import MutableModel
from loguru import logger
from pydantic import Field
from telegram_types import TelegramApiError
from telegram_types import TelegramChatId
from telegram_types import TelegramMessageId
from telegram_types import TelegramUpdateId

# latchkey injects the bot token into the path, so the method name is all that goes in the URL.
TELEGRAM_API_BASE_URL: Final[str] = "https://api.telegram.org"

TELEGRAM_CALL_HARD_TIMEOUT_SECONDS: Final[float] = 60.0
TELEGRAM_CALL_WARNING_THRESHOLD_SECONDS: Final[float] = 15.0

UPDATE_PAGE_LIMIT: Final[int] = 100


class TelegramUpdateBatch(FrozenModel):
    """Raw updates read back from Telegram, oldest first."""

    updates: tuple[dict[str, Any], ...] = Field(description="Unmodified Telegram update records")


class TelegramTransportInterface(MutableModel, ABC):
    """Defines the contract for reading and answering Telegram messages through the user's bot."""

    @abstractmethod
    def read_updates(self, offset: TelegramUpdateId) -> TelegramUpdateBatch:
        """Return updates from the given offset onward, oldest first, without acknowledging them."""

    @abstractmethod
    def acknowledge_updates_below(self, offset: TelegramUpdateId) -> None:
        """Tell Telegram every update below this offset is settled and may be dropped."""

    @abstractmethod
    def send_message(
        self,
        chat_id: TelegramChatId,
        reply_to_message_id: TelegramMessageId | None,
        text: str,
    ) -> TelegramMessageId:
        """Send a message (as a reply when given one) and return the new message's id."""


class LatchkeyTelegramTransport(TelegramTransportInterface):
    """Talks to the Telegram Bot API through latchkey, which injects the user's bot token."""

    latchkey_executable: str = Field(frozen=True, description="Name or path of the latchkey binary")

    def read_updates(self, offset: TelegramUpdateId) -> TelegramUpdateBatch:
        body = self._call_telegram_method(
            method_name="getUpdates",
            parameters={"offset": int(offset), "limit": UPDATE_PAGE_LIMIT, "timeout": 0},
        )
        raw_updates = body.get("result", [])
        if not isinstance(raw_updates, list):
            raise TelegramApiError(f"Telegram returned {type(raw_updates).__name__} for result, not a list")
        usable_updates = [update for update in raw_updates if _is_usable_update(update)]
        return TelegramUpdateBatch(
            updates=tuple(sorted(usable_updates, key=lambda update: int(update["update_id"]))),
        )

    def acknowledge_updates_below(self, offset: TelegramUpdateId) -> None:
        # Telegram has no explicit ack: asking for an offset is what confirms everything below it.
        self._call_telegram_method(
            method_name="getUpdates",
            parameters={"offset": int(offset), "limit": 1, "timeout": 0},
        )

    def send_message(
        self,
        chat_id: TelegramChatId,
        reply_to_message_id: TelegramMessageId | None,
        text: str,
    ) -> TelegramMessageId:
        parameters: dict[str, Any] = {"chat_id": int(chat_id), "text": text}
        if reply_to_message_id is not None:
            parameters["reply_to_message_id"] = int(reply_to_message_id)
        body = self._call_telegram_method(method_name="sendMessage", parameters=parameters)
        result = body.get("result", {})
        if not isinstance(result, dict) or not isinstance(result.get("message_id"), int):
            raise TelegramApiError(f"sendMessage returned no message id: {body}")
        return TelegramMessageId(result["message_id"])

    def _call_telegram_method(self, method_name: str, parameters: Mapping[str, Any]) -> dict[str, Any]:
        query_string = urllib.parse.urlencode({key: str(value) for key, value in parameters.items()})
        command = [
            self.latchkey_executable,
            "curl",
            "-s",
            f"{TELEGRAM_API_BASE_URL}/{method_name}?{query_string}",
        ]
        started_at = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=TELEGRAM_CALL_HARD_TIMEOUT_SECONDS,
                check=True,
            )
        except subprocess.TimeoutExpired as e:
            raise TelegramApiError(f"Telegram call {method_name} did not finish in time") from e
        except subprocess.CalledProcessError as e:
            raise TelegramApiError(f"Telegram call {method_name} failed: {e.stderr.strip()}") from e
        elapsed_seconds = time.monotonic() - started_at
        if elapsed_seconds > TELEGRAM_CALL_WARNING_THRESHOLD_SECONDS:
            logger.warning("Telegram call {} took {:.1f}s, which is unusually slow", method_name, elapsed_seconds)
        try:
            body = json.loads(completed.stdout)
        except json.JSONDecodeError as e:
            raise TelegramApiError(f"Telegram call {method_name} returned a body that is not JSON") from e
        if not isinstance(body, dict):
            raise TelegramApiError(f"Telegram call {method_name} returned {type(body).__name__}, not an object")
        if body.get("ok") is not True:
            raise TelegramApiError(
                f"Telegram call {method_name} was refused: {body.get('description', body.get('error', 'unknown error'))}"
            )
        return body


def _is_usable_update(update: Any) -> bool:
    if not isinstance(update, dict):
        logger.warning("Skipping a Telegram update that is not an object")
        return False
    if not isinstance(update.get("update_id"), int):
        logger.warning("Skipping a Telegram update with no usable id")
        return False
    return True
