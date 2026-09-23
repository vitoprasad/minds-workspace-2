from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any

from inbox_types import ConversationHistory
from inbox_types import SlackConversationId
from inbox_types import SlackTimestamp
from pydantic import Field
from slack_transport import SlackTransportInterface


class RecordingSlackTransport(SlackTransportInterface):
    """In-memory Slack stand-in that serves canned messages and remembers what was posted."""

    timeline_messages: tuple[dict[str, Any], ...] = Field(description="Messages in the main conversation")
    thread_messages_by_thread_ts: dict[str, tuple[dict[str, Any], ...]] = Field(
        description="Messages in each thread, keyed by the thread's timestamp",
    )
    posted_messages: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Every message posted through this transport, in order",
    )
    next_posted_ts_seed: float = Field(description="Timestamp handed out to the next posted message")

    def read_conversation_history(
        self,
        conversation_id: SlackConversationId,
        oldest_ts: SlackTimestamp,
    ) -> ConversationHistory:
        newer_messages = tuple(
            message for message in self.timeline_messages if float(message["ts"]) > float(oldest_ts)
        )
        return ConversationHistory(conversation_id=conversation_id, messages=newer_messages)

    def read_thread_replies(
        self,
        conversation_id: SlackConversationId,
        thread_ts: SlackTimestamp,
    ) -> ConversationHistory:
        return ConversationHistory(
            conversation_id=conversation_id,
            messages=self.thread_messages_by_thread_ts.get(str(thread_ts), ()),
        )

    def post_message(
        self,
        conversation_id: SlackConversationId,
        thread_ts: SlackTimestamp | None,
        text: str,
    ) -> SlackTimestamp:
        posted_ts = SlackTimestamp(f"{self.next_posted_ts_seed:.6f}")
        self.next_posted_ts_seed = self.next_posted_ts_seed + 1.0
        self.posted_messages.append(
            {
                "channel": str(conversation_id),
                "thread_ts": None if thread_ts is None else str(thread_ts),
                "text": text,
                "ts": str(posted_ts),
            }
        )
        return posted_ts

    def read_permalink(
        self,
        conversation_id: SlackConversationId,
        message_ts: SlackTimestamp,
    ) -> str | None:
        return f"https://slack.test/archives/{conversation_id}/p{str(message_ts).replace('.', '')}"


def build_user_message(message_ts: str, text: str, thread_ts: str | None) -> dict[str, Any]:
    message: dict[str, Any] = {"ts": message_ts, "text": text, "user": "U_USER", "type": "message"}
    if thread_ts is not None:
        message["thread_ts"] = thread_ts
    return message


def build_recording_transport(
    timeline_messages: Sequence[Mapping[str, Any]],
    thread_messages_by_thread_ts: Mapping[str, Sequence[Mapping[str, Any]]],
) -> RecordingSlackTransport:
    return RecordingSlackTransport(
        timeline_messages=tuple(dict(message) for message in timeline_messages),
        thread_messages_by_thread_ts={
            thread_ts: tuple(dict(message) for message in messages)
            for thread_ts, messages in thread_messages_by_thread_ts.items()
        },
        next_posted_ts_seed=9000.0,
    )
