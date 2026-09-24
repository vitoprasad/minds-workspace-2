import json
import tomllib
from pathlib import Path
from typing import Any
from typing import Final

from imbue.imbue_common.frozen_model import FrozenModel
from imbue.imbue_common.pure import pure
from pydantic import Field
from telegram_types import TelegramChatId
from telegram_types import TelegramInboxState
from telegram_types import TelegramNotConfiguredError
from telegram_types import TelegramRequest
from telegram_types import TelegramStateError

TELEGRAM_CONFIGURATION_FILE_NAME: Final[str] = "telegram.toml"
TELEGRAM_STATE_FILE_NAME: Final[str] = "telegram_state.json"
TELEGRAM_RAW_RECORD_DIRECTORY_NAME: Final[str] = "telegram_raw"


class TelegramInboxPaths(FrozenModel):
    """Where the Telegram inbox keeps its claimed chat, its offset state, and its raw archive."""

    root_directory: Path = Field(description="Directory holding everything this inbox stores")

    @property
    def configuration_path(self) -> Path:
        return self.root_directory / TELEGRAM_CONFIGURATION_FILE_NAME

    @property
    def state_path(self) -> Path:
        return self.root_directory / TELEGRAM_STATE_FILE_NAME

    @property
    def raw_record_directory(self) -> Path:
        return self.root_directory / TELEGRAM_RAW_RECORD_DIRECTORY_NAME


@pure
def build_telegram_inbox_paths(root_directory: Path) -> TelegramInboxPaths:
    return TelegramInboxPaths(root_directory=root_directory)


def write_configuration(paths: TelegramInboxPaths, chat_id: TelegramChatId) -> None:
    paths.root_directory.mkdir(parents=True, exist_ok=True)
    try:
        paths.configuration_path.write_text(
            f"# The one Telegram chat this inbox accepts requests from.\nchat_id = {int(chat_id)}\n"
        )
    except OSError as e:
        raise TelegramStateError(f"Cannot write Telegram inbox configuration: {paths.configuration_path}") from e


def load_chat_id(paths: TelegramInboxPaths) -> TelegramChatId:
    if not paths.configuration_path.exists():
        raise TelegramNotConfiguredError(
            f"No Telegram chat has been claimed yet (expected {paths.configuration_path})"
        )
    try:
        raw_configuration = tomllib.loads(paths.configuration_path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise TelegramStateError(f"Cannot read Telegram inbox configuration: {paths.configuration_path}") from e
    chat_id = raw_configuration.get("chat_id")
    if not isinstance(chat_id, int):
        raise TelegramStateError(f"Telegram configuration has no usable chat_id: {paths.configuration_path}")
    return TelegramChatId(chat_id)


def write_state(paths: TelegramInboxPaths, state: TelegramInboxState) -> None:
    paths.root_directory.mkdir(parents=True, exist_ok=True)
    try:
        paths.state_path.write_text(json.dumps(state.model_dump(mode="json"), indent=2) + "\n")
    except OSError as e:
        raise TelegramStateError(f"Cannot write Telegram inbox state: {paths.state_path}") from e


def load_state(paths: TelegramInboxPaths) -> TelegramInboxState:
    if not paths.state_path.exists():
        raise TelegramNotConfiguredError(f"The Telegram inbox has no stored state yet (expected {paths.state_path})")
    try:
        raw_state = json.loads(paths.state_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise TelegramStateError(f"Cannot read Telegram inbox state: {paths.state_path}") from e
    return TelegramInboxState.model_validate(raw_state)


def persist_raw_request(paths: TelegramInboxPaths, request: TelegramRequest) -> Path:
    """Archive the request's untouched Telegram record before anything acts on it."""
    paths.raw_record_directory.mkdir(parents=True, exist_ok=True)
    record_path = paths.raw_record_directory / f"{int(request.update_id)}.json"
    archived_record: dict[str, Any] = {
        "update_id": int(request.update_id),
        "chat_id": int(request.chat_id),
        "message_id": int(request.message_id),
        "telegram_update": request.raw_update,
    }
    try:
        record_path.write_text(json.dumps(archived_record, indent=2, sort_keys=True) + "\n")
    except OSError as e:
        raise TelegramStateError(f"Cannot archive the raw Telegram update: {record_path}") from e
    return record_path
