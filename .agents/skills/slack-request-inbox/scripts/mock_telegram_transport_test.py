import subprocess
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any

from pydantic import Field
from telegram_listener import AgentWakerInterface
from telegram_transport import TelegramTransportInterface
from telegram_transport import TelegramUpdateBatch
from telegram_types import TelegramChatId
from telegram_types import TelegramMessageId
from telegram_types import TelegramUpdateId


class RecordingTelegramTransport(TelegramTransportInterface):
    """In-memory Telegram stand-in that drops acknowledged updates exactly as Telegram does."""

    available_updates: list[dict[str, Any]] = Field(description="Updates the bot still has queued")
    sent_messages: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Every message sent through this transport, in order",
    )
    read_offsets: list[int] = Field(
        default_factory=list,
        description="Offsets read_updates was called with, in order",
    )
    next_message_id: int = Field(description="Message id handed out to the next sent message")

    def read_updates(self, offset: TelegramUpdateId, long_poll_seconds: int) -> TelegramUpdateBatch:
        self.read_offsets.append(int(offset))
        return TelegramUpdateBatch(
            updates=tuple(
                update
                for update in sorted(self.available_updates, key=lambda candidate: candidate["update_id"])
                if update["update_id"] >= int(offset)
            ),
        )

    def acknowledge_updates_below(self, offset: TelegramUpdateId) -> None:
        self.available_updates = [update for update in self.available_updates if update["update_id"] >= int(offset)]

    def send_message(
        self,
        chat_id: TelegramChatId,
        reply_to_message_id: TelegramMessageId | None,
        text: str,
    ) -> TelegramMessageId:
        message_id = TelegramMessageId(self.next_message_id)
        self.next_message_id = self.next_message_id + 1
        self.sent_messages.append(
            {
                "chat_id": int(chat_id),
                "reply_to_message_id": None if reply_to_message_id is None else int(reply_to_message_id),
                "text": text,
                "message_id": int(message_id),
            }
        )
        return message_id


class RecordingAgentWaker(AgentWakerInterface):
    """Counts how many times the listener asked for the agent, without starting one."""

    wake_count: int = Field(default=0, description="How many times wake was called")

    def wake(self) -> None:
        self.wake_count = self.wake_count + 1


class FailingAgentWaker(AgentWakerInterface):
    """A waker that cannot start the agent, for checking the request survives a failed wake."""

    wake_count: int = Field(default=0, description="How many times wake was called")

    def wake(self) -> None:
        self.wake_count = self.wake_count + 1
        raise subprocess.CalledProcessError(returncode=1, cmd=["run_automation.sh"])


def build_telegram_update(update_id: int, chat_id: int, message_id: int, text: str) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id, "is_bot": False},
            "text": text,
        },
    }


def build_recording_telegram_transport(
    available_updates: Sequence[Mapping[str, Any]],
) -> RecordingTelegramTransport:
    return RecordingTelegramTransport(
        available_updates=[dict(update) for update in available_updates],
        next_message_id=5000,
    )
