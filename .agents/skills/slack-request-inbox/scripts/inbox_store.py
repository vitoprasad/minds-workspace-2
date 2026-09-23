import json
import tomllib
from pathlib import Path
from typing import Any
from typing import Final

from imbue.imbue_common.frozen_model import FrozenModel
from imbue.imbue_common.model_update import to_update
from imbue.imbue_common.pure import pure
from inbox_types import InboxNotConfiguredError
from inbox_types import InboxRequest
from inbox_types import InboxState
from inbox_types import InboxStateError
from inbox_types import SlackConversationId
from inbox_types import SlackTimestamp
from pydantic import Field

# Anchored to the repo root rather than the working directory, so the inbox stores its state in
# one place whether it is run from the scripts directory or from a cron job.
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[4]

DEFAULT_INBOX_ROOT: Final[Path] = REPO_ROOT / "data" / ".skills" / "slack-request-inbox"

CONFIGURATION_FILE_NAME: Final[str] = "config.toml"
STATE_FILE_NAME: Final[str] = "state.json"
RAW_RECORD_DIRECTORY_NAME: Final[str] = "raw"

MAX_REMEMBERED_TIMESTAMP_COUNT: Final[int] = 400


class InboxPaths(FrozenModel):
    """Where the inbox keeps its configuration, its watermark state, and its raw message archive."""

    root_directory: Path = Field(description="Directory holding everything this inbox stores")

    @property
    def configuration_path(self) -> Path:
        return self.root_directory / CONFIGURATION_FILE_NAME

    @property
    def state_path(self) -> Path:
        return self.root_directory / STATE_FILE_NAME

    @property
    def raw_record_directory(self) -> Path:
        return self.root_directory / RAW_RECORD_DIRECTORY_NAME


@pure
def build_inbox_paths(root_directory: Path) -> InboxPaths:
    return InboxPaths(root_directory=root_directory)


def write_configuration(paths: InboxPaths, conversation_id: SlackConversationId) -> None:
    paths.root_directory.mkdir(parents=True, exist_ok=True)
    try:
        paths.configuration_path.write_text(
            f'# The Slack conversation this inbox watches.\nconversation_id = "{conversation_id}"\n'
        )
    except OSError as e:
        raise InboxStateError(f"Cannot write inbox configuration: {paths.configuration_path}") from e


def load_conversation_id(paths: InboxPaths) -> SlackConversationId:
    if not paths.configuration_path.exists():
        raise InboxNotConfiguredError(
            f"No Slack conversation is configured yet (expected {paths.configuration_path})"
        )
    try:
        raw_configuration = tomllib.loads(paths.configuration_path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise InboxStateError(f"Cannot read inbox configuration: {paths.configuration_path}") from e
    conversation_id = raw_configuration.get("conversation_id")
    if not isinstance(conversation_id, str) or not conversation_id:
        raise InboxStateError(f"Inbox configuration has no usable conversation_id: {paths.configuration_path}")
    return SlackConversationId(conversation_id)


def write_state(paths: InboxPaths, state: InboxState) -> None:
    paths.root_directory.mkdir(parents=True, exist_ok=True)
    try:
        paths.state_path.write_text(json.dumps(state.model_dump(mode="json"), indent=2) + "\n")
    except OSError as e:
        raise InboxStateError(f"Cannot write inbox state: {paths.state_path}") from e


def load_state(paths: InboxPaths) -> InboxState:
    if not paths.state_path.exists():
        raise InboxNotConfiguredError(f"The inbox has no stored state yet (expected {paths.state_path})")
    try:
        raw_state = json.loads(paths.state_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise InboxStateError(f"Cannot read inbox state: {paths.state_path}") from e
    return InboxState.model_validate(raw_state)


def persist_raw_request(paths: InboxPaths, request: InboxRequest) -> Path:
    """Archive the request's untouched Slack record, so nothing is lost if the reply goes wrong."""
    paths.raw_record_directory.mkdir(parents=True, exist_ok=True)
    record_path = paths.raw_record_directory / f"{request.request_ts}.json"
    archived_record: dict[str, Any] = {
        "request_ts": str(request.request_ts),
        "thread_ts": str(request.thread_ts),
        "permalink": request.permalink,
        "slack_message": request.raw_message,
    }
    try:
        record_path.write_text(json.dumps(archived_record, indent=2, sort_keys=True) + "\n")
    except OSError as e:
        raise InboxStateError(f"Cannot archive the raw Slack message: {record_path}") from e
    return record_path


@pure
def with_handled_request(state: InboxState, request_ts: SlackTimestamp) -> InboxState:
    if request_ts in state.handled_ts:
        return state
    return state.model_copy_update(
        to_update(state.field_ref().handled_ts, state.handled_ts + (request_ts,)),
    )


@pure
def with_posted_message(state: InboxState, posted_ts: SlackTimestamp) -> InboxState:
    if posted_ts in state.posted_ts:
        return state
    return state.model_copy_update(
        to_update(state.field_ref().posted_ts, state.posted_ts + (posted_ts,)),
    )


@pure
def with_watched_thread(state: InboxState, thread_ts: SlackTimestamp) -> InboxState:
    if thread_ts in state.watched_thread_ts:
        return state
    return state.model_copy_update(
        to_update(state.field_ref().watched_thread_ts, state.watched_thread_ts + (thread_ts,)),
    )


@pure
def with_watermark(state: InboxState, oldest_ts: SlackTimestamp) -> InboxState:
    return state.model_copy_update(
        to_update(state.field_ref().oldest_ts, oldest_ts),
    )
