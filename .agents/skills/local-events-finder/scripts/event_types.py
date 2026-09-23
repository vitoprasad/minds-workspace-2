from datetime import datetime
from enum import auto
from typing import Final

from imbue.imbue_common.enums import UpperCaseStrEnum
from imbue.imbue_common.frozen_model import FrozenModel
from imbue.imbue_common.primitives import NonEmptyStr, NonNegativeFloat, NonNegativeInt
from pydantic import Field


class LocalEventsError(Exception):
    """Base exception for the local events finder."""

    ...


class EventSourceError(LocalEventsError, OSError):
    """Raised when an event source cannot be read or parsed."""

    ...


class CalendarReadError(LocalEventsError, OSError):
    """Raised when the user's calendar cannot be read."""

    ...


class EventSourceName(UpperCaseStrEnum):
    """The platform an event listing came from."""

    PARTIFUL = auto()
    LUMA = auto()


class EventTitle(NonEmptyStr):
    """The display title of an event."""

    ...


class InterestKeyword(NonEmptyStr):
    """A lowercased keyword describing something the user is interested in."""

    ...


class MilesFromHome(NonNegativeFloat):
    """Great-circle distance from the user's home coordinate, in miles."""

    ...


class MatchScore(NonNegativeFloat):
    """How well an event fits the user's profile. Higher is a better fit."""

    ...


class GeoPoint(FrozenModel):
    """A latitude/longitude pair on the earth's surface."""

    latitude: float = Field(
        ge=-90.0, le=90.0, description="Degrees north of the equator"
    )
    longitude: float = Field(
        ge=-180.0, le=180.0, description="Degrees east of the prime meridian"
    )


class EventCost(FrozenModel):
    """What it costs to attend an event, and whether places remain."""

    is_free: bool = Field(description="Whether the event costs nothing to attend")
    price_cents: NonNegativeInt | None = Field(
        description="Ticket price in cents, when the source states one"
    )
    is_sold_out: bool = Field(
        description="Whether the source reports no remaining places"
    )
    spots_remaining: NonNegativeInt | None = Field(
        description="Places left, when the source states a count"
    )


class EventLocation(FrozenModel):
    """Where an event happens, as precisely as its source discloses."""

    venue_name: str = Field(
        description="Name of the venue, empty when the source hides it"
    )
    locality: str = Field(description="City or town, e.g. 'San Francisco'")
    neighborhood: str = Field(
        description="Sub-locality, empty when the source hides it"
    )
    coordinate: GeoPoint | None = Field(
        description="Venue coordinate, when the source discloses one"
    )


class LocalEvent(FrozenModel):
    """One normalized public event listing, from any source."""

    source: EventSourceName = Field(description="Which platform this listing came from")
    source_event_id: NonEmptyStr = Field(
        description="The source's own identifier for this event"
    )
    source_url: NonEmptyStr = Field(
        description="Canonical link back to the listing on its source"
    )
    title: EventTitle = Field(description="The event's display title")
    description: str = Field(
        description="The event's own blurb, empty when the source omits one"
    )
    host_name: str = Field(description="Who is hosting, empty when the source hides it")
    starts_at: datetime = Field(
        description="Start time, timezone-aware and UTC-anchored"
    )
    location: EventLocation = Field(description="Where the event happens")
    cost: EventCost = Field(description="Price and availability")
    attending_count: NonNegativeInt | None = Field(
        description="How many people are going, when disclosed"
    )
    is_recurring_series: bool = Field(
        description="Whether this listing looks like part of a repeating series"
    )


class CalendarCommitment(FrozenModel):
    """Something already on the user's calendar, used to avoid clashes and repeats."""

    title: str = Field(description="The calendar entry's title")
    starts_at: datetime = Field(
        description="Start time, timezone-aware and UTC-anchored"
    )
    ends_at: datetime = Field(description="End time, timezone-aware and UTC-anchored")
    # An all-day entry (a birthday, a holiday, a hotel stay) marks a date rather than
    # occupying the hours of it, so it must never block an evening.
    is_all_day: bool = Field(
        description="Whether the entry marks a whole date rather than a time"
    )


class UserProfile(FrozenModel):
    """What the user's own calendar reveals about where they are and what they do."""

    home_locality: NonEmptyStr = Field(
        description="The city the user's in-person commitments cluster in"
    )
    timezone_name: NonEmptyStr = Field(
        description="IANA timezone of the user's primary calendar"
    )
    home_coordinate: GeoPoint | None = Field(
        description="Approximate home coordinate, when derivable"
    )
    interest_keywords: tuple[InterestKeyword, ...] = Field(
        description="Keywords drawn from what the user repeatedly does, most frequent first"
    )
    commitments: tuple[CalendarCommitment, ...] = Field(
        description="Everything already scheduled in the window"
    )


class ScoredEvent(FrozenModel):
    """An event paired with why it was chosen and how well it fits."""

    event: LocalEvent = Field(description="The underlying listing")
    score: MatchScore = Field(description="How well the event fits the profile")
    matched_keywords: tuple[InterestKeyword, ...] = Field(
        description="Profile keywords this event matched"
    )
    distance_miles: MilesFromHome | None = Field(
        description="Distance from home, when both coordinates are known"
    )
    reasons: tuple[str, ...] = Field(
        description="Plain-English reasons this event was ranked where it was"
    )


class RejectionReason(UpperCaseStrEnum):
    """Why a candidate event was excluded before ranking."""

    SOLD_OUT = auto()
    TOO_FAR = auto()
    ALREADY_COMMITTED = auto()
    CLASHES_WITH_COMMITMENT = auto()
    OUTSIDE_TIME_WINDOW = auto()
    PREVIOUSLY_DECLINED = auto()
    NO_INTEREST_MATCH = auto()


class RejectedEvent(FrozenModel):
    """An event that was filtered out, kept so the exclusion stays inspectable."""

    event: LocalEvent = Field(description="The excluded listing")
    reason: RejectionReason = Field(description="Why it was excluded")


class EventSelection(FrozenModel):
    """The outcome of ranking a batch of candidate events against a profile."""

    selected: tuple[ScoredEvent, ...] = Field(
        description="Events worth surfacing, best fit first"
    )
    rejected: tuple[RejectedEvent, ...] = Field(
        description="Events filtered out, with the reason for each"
    )


# Calendar entries whose titles describe work or admin rather than an interest. Tokens are
# matched case-insensitively against whole words when deriving interest keywords.
NON_INTEREST_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "1on1",
        "birthday",
        "call",
        "canceled",
        "cancelled",
        "chat",
        "confirmation",
        "flight",
        "hold",
        "hr",
        "intro",
        "interview",
        "meet",
        "meeting",
        "min",
        "minutes",
        "onsite",
        "phone",
        "prep",
        "recruiter",
        "reservation",
        "screen",
        "standup",
        "sync",
        "train",
        "vs",
        "with",
        "zoom",
    }
)

# Words too common to carry interest signal in either a calendar entry or an event title.
STOP_WORDS: Final[frozenset[str]] = frozenset(
    {
        "a",
        "an",
        "and",
        "at",
        "by",
        "day",
        "for",
        "from",
        "in",
        "night",
        "of",
        "on",
        "or",
        "the",
        "to",
        "up",
        "week",
        "with",
    }
)

# City and venue words appear in so many titles on both sides that matching on one says
# nothing -- everything in a San Francisco feed contains "San Francisco".
PLACE_WORDS: Final[frozenset[str]] = frozenset(
    {
        "bay",
        "berkeley",
        "brooklyn",
        "city",
        "dc",
        "francisco",
        "la",
        "nyc",
        "oakland",
        "san",
        "sf",
        "york",
    }
)

# Words that describe when something happens or how it is delivered, rather than what
# it is about. They match across unrelated events and explain nothing.
TIMING_WORDS: Final[frozenset[str]] = frozenset(
    {
        "afternoon",
        "annual",
        "evening",
        "fall",
        "monthly",
        "morning",
        "online",
        "spring",
        "stay",
        "summer",
        "today",
        "tomorrow",
        "tonight",
        "weekend",
        "weekly",
        "winter",
    }
)
