import json
import subprocess
import time
import urllib.parse
from abc import ABC
from abc import abstractmethod
from collections.abc import Mapping
from enum import auto
from typing import Any
from typing import Final
from typing import assert_never

from imbue.imbue_common.enums import UpperCaseStrEnum
from imbue.imbue_common.mutable_model import MutableModel
from imbue.imbue_common.pure import pure
from inbox_types import ConversationHistory
from inbox_types import IGNORED_MESSAGE_SUBTYPES
from inbox_types import SlackApiError
from inbox_types import SlackConversationId
from inbox_types import SlackTimestamp
from loguru import logger
from pydantic import Field

SLACK_API_BASE_URL: Final[str] = "https://slack.com/api"

# Two-threshold timeouts for the Slack calls: the hard limit is "this is definitely broken",
# the warning threshold is "this is getting slow and we should notice before it breaks".
SLACK_CALL_HARD_TIMEOUT_SECONDS: Final[float] = 60.0
SLACK_CALL_WARNING_THRESHOLD_SECONDS: Final[float] = 15.0

# Slack caps a single history page at 1000; 200 is plenty for a personal request inbox.
HISTORY_PAGE_LIMIT: Final[int] = 200


class SlackHttpMethod(UpperCaseStrEnum):
    """How a Slack API method has to be called.

    Slack's read methods (conversations.history, conversations.replies, chat.getPermalink) reject a
    JSON body with `invalid_arguments` and must be called as a query string; the write methods take JSON.
    """

    GET = auto()
    POST = auto()


@pure
def _build_curl_arguments(
    method_name: str,
    parameters: Mapping[str, Any],
    http_method: SlackHttpMethod,
) -> list[str]:
    method_url = f"{SLACK_API_BASE_URL}/{method_name}"
    match http_method:
        case SlackHttpMethod.GET:
            query_string = urllib.parse.urlencode({key: str(value) for key, value in parameters.items()})
            return [f"{method_url}?{query_string}"]
        case SlackHttpMethod.POST:
            return [
                "-X",
                "POST",
                method_url,
                "-H",
                "Content-Type: application/json; charset=utf-8",
                "-d",
                json.dumps(dict(parameters)),
            ]
        case _ as unreachable:
            assert_never(unreachable)


class SlackTransportInterface(MutableModel, ABC):
    """Defines the contract for reading and writing Slack messages on the user's behalf."""

    @abstractmethod
    def read_conversation_history(
        self,
        conversation_id: SlackConversationId,
        oldest_ts: SlackTimestamp,
    ) -> ConversationHistory:
        """Return the conversation's messages newer than the given timestamp, oldest first."""

    @abstractmethod
    def read_thread_replies(
        self,
        conversation_id: SlackConversationId,
        thread_ts: SlackTimestamp,
    ) -> ConversationHistory:
        """Return every message in one thread, oldest first, including the thread's parent."""

    @abstractmethod
    def post_message(
        self,
        conversation_id: SlackConversationId,
        thread_ts: SlackTimestamp | None,
        text: str,
    ) -> SlackTimestamp:
        """Post a message (in a thread when given one) and return the new message's timestamp."""

    @abstractmethod
    def read_permalink(
        self,
        conversation_id: SlackConversationId,
        message_ts: SlackTimestamp,
    ) -> str | None:
        """Return a Slack permalink for one message, or None when Slack will not give one."""


class LatchkeySlackTransport(SlackTransportInterface):
    """Talks to the Slack API through latchkey, which injects the user's credentials."""

    latchkey_executable: str = Field(frozen=True, description="Name or path of the latchkey binary")

    def read_conversation_history(
        self,
        conversation_id: SlackConversationId,
        oldest_ts: SlackTimestamp,
    ) -> ConversationHistory:
        body = self._call_slack_method(
            method_name="conversations.history",
            parameters={
                "channel": str(conversation_id),
                "oldest": str(oldest_ts),
                "limit": HISTORY_PAGE_LIMIT,
                "inclusive": "false",
            },
            http_method=SlackHttpMethod.GET,
        )
        return _build_conversation_history(conversation_id=conversation_id, body=body)

    def read_thread_replies(
        self,
        conversation_id: SlackConversationId,
        thread_ts: SlackTimestamp,
    ) -> ConversationHistory:
        body = self._call_slack_method(
            method_name="conversations.replies",
            parameters={
                "channel": str(conversation_id),
                "ts": str(thread_ts),
                "limit": HISTORY_PAGE_LIMIT,
            },
            http_method=SlackHttpMethod.GET,
        )
        return _build_conversation_history(conversation_id=conversation_id, body=body)

    def post_message(
        self,
        conversation_id: SlackConversationId,
        thread_ts: SlackTimestamp | None,
        text: str,
    ) -> SlackTimestamp:
        parameters: dict[str, Any] = {"channel": str(conversation_id), "text": text}
        if thread_ts is not None:
            parameters["thread_ts"] = str(thread_ts)
        body = self._call_slack_method(
            method_name="chat.postMessage",
            parameters=parameters,
            http_method=SlackHttpMethod.POST,
        )
        posted_ts = body.get("ts")
        if not isinstance(posted_ts, str):
            raise SlackApiError(f"chat.postMessage returned no timestamp: {body}")
        return SlackTimestamp(posted_ts)

    def read_permalink(
        self,
        conversation_id: SlackConversationId,
        message_ts: SlackTimestamp,
    ) -> str | None:
        try:
            # chat.getPermalink is one of the read methods Slack only accepts as a query string.
            body = self._call_slack_method(
                method_name="chat.getPermalink",
                parameters={"channel": str(conversation_id), "message_ts": str(message_ts)},
                http_method=SlackHttpMethod.GET,
            )
        except SlackApiError as e:
            logger.warning("Failed to resolve a Slack permalink, continuing without one: {}", e)
            return None
        permalink = body.get("permalink")
        return permalink if isinstance(permalink, str) else None

    def _call_slack_method(
        self,
        method_name: str,
        parameters: Mapping[str, Any],
        http_method: SlackHttpMethod,
    ) -> dict[str, Any]:
        command = [self.latchkey_executable, "curl", "-s"] + _build_curl_arguments(
            method_name=method_name,
            parameters=parameters,
            http_method=http_method,
        )
        started_at = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=SLACK_CALL_HARD_TIMEOUT_SECONDS,
                check=True,
            )
        except subprocess.TimeoutExpired as e:
            raise SlackApiError(f"Slack call {method_name} did not finish in time") from e
        except subprocess.CalledProcessError as e:
            raise SlackApiError(f"Slack call {method_name} failed: {e.stderr.strip()}") from e
        elapsed_seconds = time.monotonic() - started_at
        if elapsed_seconds > SLACK_CALL_WARNING_THRESHOLD_SECONDS:
            logger.warning("Slack call {} took {:.1f}s, which is unusually slow", method_name, elapsed_seconds)
        try:
            body = json.loads(completed.stdout)
        except json.JSONDecodeError as e:
            raise SlackApiError(f"Slack call {method_name} returned a body that is not JSON") from e
        if not isinstance(body, dict):
            raise SlackApiError(f"Slack call {method_name} returned {type(body).__name__}, not an object")
        if body.get("ok") is not True:
            raise SlackApiError(f"Slack call {method_name} was refused: {body.get('error', 'unknown error')}")
        return body


def _build_conversation_history(
    conversation_id: SlackConversationId,
    body: Mapping[str, Any],
) -> ConversationHistory:
    raw_messages = body.get("messages", [])
    if not isinstance(raw_messages, list):
        raise SlackApiError(f"Slack returned {type(raw_messages).__name__} for messages, not a list")
    usable_messages = [message for message in raw_messages if _is_usable_message(message)]
    return ConversationHistory(
        conversation_id=conversation_id,
        messages=tuple(sorted(usable_messages, key=lambda message: float(message["ts"]))),
    )


def _is_usable_message(message: Any) -> bool:
    if not isinstance(message, dict):
        logger.warning("Skipping a Slack history entry that is not an object")
        return False
    message_ts = message.get("ts")
    if not isinstance(message_ts, str):
        logger.warning("Skipping a Slack message with no usable timestamp")
        return False
    try:
        float(message_ts)
    except ValueError:
        logger.warning("Skipping a Slack message whose timestamp does not parse: {}", message_ts)
        return False
    if message.get("subtype") in IGNORED_MESSAGE_SUBTYPES:
        return False
    return True
