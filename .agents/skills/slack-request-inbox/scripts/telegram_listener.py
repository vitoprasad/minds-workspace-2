import argparse
import subprocess
import time
from abc import ABC
from abc import abstractmethod
from pathlib import Path
from typing import Final

from imbue.imbue_common.frozen_model import FrozenModel
from imbue.imbue_common.mutable_model import MutableModel
from inbox_store import DEFAULT_INBOX_ROOT
from inbox_store import REPO_ROOT
from loguru import logger
from pydantic import Field
from telegram_inbox import LATCHKEY_EXECUTABLE
from telegram_inbox import LISTENER_LONG_POLL_SECONDS
from telegram_inbox import ingest_once
from telegram_store import TelegramInboxPaths
from telegram_store import build_telegram_inbox_paths
from telegram_transport import LatchkeyTelegramTransport
from telegram_transport import TelegramTransportInterface
from telegram_types import TelegramApiError
from telegram_types import TelegramNotConfiguredError

RUN_AUTOMATION_SCRIPT: Final[Path] = REPO_ROOT / "system" / "libs" / "automations" / "run_automation.sh"

INBOX_SKILL_NAME: Final[str] = "slack-request-inbox"

# Pause after a failed poll, so a Telegram outage or a revoked permission becomes a slow retry
# rather than a hot loop hammering the API.
ERROR_BACKOFF_SECONDS: Final[float] = 30.0

# Pause when Telegram has not been claimed yet: the listener stays up so it starts working the
# moment a chat is claimed, without spending a call per second until then.
UNCONFIGURED_BACKOFF_SECONDS: Final[float] = 60.0

# The agent is woken at most this often. A burst of messages in one minute is one wake, and the
# agent answers every queued request in that single run.
MIN_SECONDS_BETWEEN_WAKES: Final[float] = 20.0

WAKE_HARD_TIMEOUT_SECONDS: Final[float] = 300.0


class ListenerSettings(FrozenModel):
    """How the listener polls and how often it may wake the agent."""

    inbox_root: Path = Field(description="Directory holding the inbox's configuration and queue")
    long_poll_seconds: int = Field(description="Seconds Telegram holds each poll open")
    min_seconds_between_wakes: float = Field(description="Floor on how often the agent is woken")


class AgentWakerInterface(MutableModel, ABC):
    """Defines the contract for handing the queued requests to an agent."""

    @abstractmethod
    def wake(self) -> None:
        """Start (or re-trigger) the agent that answers the queue. Raises on failure to wake."""


class AutomationAgentWaker(AgentWakerInterface):
    """Wakes the inbox's singleton automation agent, keeping its chat warm between requests.

    The clear-and-retrigger cycle the scheduled runner uses costs about ninety seconds, which is
    most of the delay the user actually feels. A request line wants the opposite of a fresh context
    anyway: keeping the conversation warm is what makes a follow-up message make sense.
    """

    def wake(self) -> None:
        subprocess.run(
            ["bash", str(RUN_AUTOMATION_SCRIPT), INBOX_SKILL_NAME, "--no-clear"],
            cwd=str(REPO_ROOT),
            timeout=WAKE_HARD_TIMEOUT_SECONDS,
            check=True,
        )


def listen_forever(
    transport: TelegramTransportInterface,
    paths: TelegramInboxPaths,
    waker: AgentWakerInterface,
    settings: ListenerSettings,
    # None means run until killed; a count is what lets a test drive a bounded number of polls.
    max_poll_count: int | None,
) -> None:
    """Hold a long poll open on Telegram and wake the agent the moment a request lands."""
    last_wake_monotonic = 0.0
    completed_poll_count = 0
    while max_poll_count is None or completed_poll_count < max_poll_count:
        completed_poll_count = completed_poll_count + 1
        try:
            report = ingest_once(
                transport=transport,
                paths=paths,
                long_poll_seconds=settings.long_poll_seconds,
            )
        except TelegramNotConfiguredError:
            logger.debug("No Telegram chat claimed yet; waiting before looking again")
            time.sleep(UNCONFIGURED_BACKOFF_SECONDS)
            continue
        except TelegramApiError as e:
            logger.warning("Telegram poll failed, backing off: {}", e)
            time.sleep(ERROR_BACKOFF_SECONDS)
            continue

        if not report.newly_queued_requests:
            continue

        logger.info(
            "Queued {} new Telegram request(s); {} waiting",
            len(report.newly_queued_requests),
            report.pending_request_count,
        )
        seconds_since_wake = time.monotonic() - last_wake_monotonic
        if seconds_since_wake < settings.min_seconds_between_wakes:
            time.sleep(settings.min_seconds_between_wakes - seconds_since_wake)
        try:
            waker.wake()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            # The request stays queued, so the every-minute scheduled check still picks it up.
            logger.warning("Could not wake the inbox agent; leaving it to the scheduled check: {}", e)
        last_wake_monotonic = time.monotonic()


def main() -> None:
    parser = argparse.ArgumentParser(description="Wake the request inbox the moment a Telegram message arrives.")
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_INBOX_ROOT,
        help="Directory holding the inbox's configuration and queue",
    )
    parser.add_argument(
        "--long-poll-seconds",
        type=int,
        default=LISTENER_LONG_POLL_SECONDS,
        help="Seconds Telegram holds each poll open waiting for a message",
    )
    arguments = parser.parse_args()
    listen_forever(
        transport=LatchkeyTelegramTransport(latchkey_executable=LATCHKEY_EXECUTABLE),
        paths=build_telegram_inbox_paths(root_directory=arguments.root),
        waker=AutomationAgentWaker(),
        max_poll_count=None,
        settings=ListenerSettings(
            inbox_root=arguments.root,
            long_poll_seconds=arguments.long_poll_seconds,
            min_seconds_between_wakes=MIN_SECONDS_BETWEEN_WAKES,
        ),
    )


if __name__ == "__main__":
    main()
