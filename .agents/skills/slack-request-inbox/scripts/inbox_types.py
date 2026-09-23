from typing import Any
from typing import Final

from imbue.imbue_common.frozen_model import FrozenModel
from imbue.imbue_common.primitives import NonEmptyStr
from pydantic import Field

# Slack message subtypes that are channel bookkeeping rather than something the user typed.
IGNORED_MESSAGE_SUBTYPES: Final[frozenset[str]] = frozenset(
    {
        "channel_join",
        "channel_leave",
        "channel_topic",
        "channel_purpose",
        "channel_name",
        "message_changed",
        "message_deleted",
        "thread_broadcast",
        "bot_message",
    }
)


class SlackRequestInboxError(Exception):
    """Base exception for the Slack request inbox."""

    ...


class SlackApiError(SlackRequestInboxError, OSError):
    """Raised when the Slack API refuses a call or returns an unusable body."""

    ...


class InboxStateError(SlackRequestInboxError, OSError):
    """Raised when the inbox's own stored state cannot be read or written."""

    ...


class InboxNotConfiguredError(SlackRequestInboxError, OSError):
    """Raised when the inbox is used before a conversation has been chosen."""

    ...


class SlackConversationId(NonEmptyStr):
    """A Slack conversation id (a channel, group, or direct-message id)."""

    ...


class SlackTimestamp(NonEmptyStr):
    """A Slack message timestamp, which doubles as the message's id within its conversation."""

    ...


class SlackUserId(NonEmptyStr):
    """A Slack user id."""

    ...


class InboxRequest(FrozenModel):
    """One message in the watched conversation that is waiting to be acted on."""

    request_ts: SlackTimestamp = Field(description="Timestamp of the message that carries the request")
    thread_ts: SlackTimestamp = Field(description="Timestamp of the thread the reply belongs in")
    author_user_id: SlackUserId = Field(description="Slack user who sent the message")
    text: str = Field(description="The message text exactly as Slack returned it")
    permalink: str | None = Field(description="Slack permalink to the message, when one was resolved")
    raw_message: dict[str, Any] = Field(description="The unmodified Slack message record")


class InboxState(FrozenModel):
    """What the inbox remembers between runs so it neither loses nor re-answers a request."""

    oldest_ts: SlackTimestamp = Field(description="Watermark: every message at or before this is settled")
    handled_ts: tuple[SlackTimestamp, ...] = Field(description="Requests already answered")
    posted_ts: tuple[SlackTimestamp, ...] = Field(description="Messages this inbox itself posted")
    watched_thread_ts: tuple[SlackTimestamp, ...] = Field(
        description="Threads already replied in, still polled so follow-up messages inside them are seen"
    )


class ConversationHistory(FrozenModel):
    """Messages read back from one Slack conversation, oldest first."""

    conversation_id: SlackConversationId = Field(description="Conversation the messages came from")
    messages: tuple[dict[str, Any], ...] = Field(description="Raw Slack message records, ascending by timestamp")
