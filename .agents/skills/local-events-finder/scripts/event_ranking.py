import math
import re
from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Final

from event_types import (
    NON_INTEREST_TOKENS,
    PLACE_WORDS,
    STOP_WORDS,
    TIMING_WORDS,
    CalendarCommitment,
    EventSelection,
    GeoPoint,
    InterestKeyword,
    LocalEvent,
    MatchScore,
    MilesFromHome,
    RejectedEvent,
    RejectionReason,
    ScoredEvent,
    UserProfile,
)
from imbue.imbue_common.pure import pure

EARTH_RADIUS_MILES: Final[float] = 3958.8

# A word appearing in more than this share of calendar entries is about the person, not
# about anything they are interested in.
UBIQUITOUS_WORD_SHARE: Final[float] = 0.25

# A listing counts as recurring when its title advertises a repeat -- a season, a week
# number, a volume, a month name, or an ordinal edition.
RECURRING_TITLE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(szn|season|week\s*\d|vol\.?\s*\d|no\.?\s*\d|#\d+|"
    r"weekly|monthly|club|january|february|march|april|may|june|july|"
    r"august|september|october|november|december)\b",
    re.IGNORECASE,
)

WORD_PATTERN: Final[re.Pattern[str]] = re.compile(r"[a-z0-9぀-ヿ一-鿿]+")

# Weights for the ranking formula. Recurrence outranks a single keyword hit on purpose:
# repeat exposure to the same people is what the product is for.
KEYWORD_MATCH_WEIGHT: Final[float] = 1.0
RECURRING_SERIES_BONUS: Final[float] = 1.5
SMALL_ROOM_BONUS: Final[float] = 0.75
FREE_EVENT_BONUS: Final[float] = 0.25
NEAR_CAPACITY_BONUS: Final[float] = 0.4
DISTANCE_PENALTY_PER_MILE: Final[float] = 0.12

# Above this headcount an event stops being a room where anyone gets talked to.
SMALL_ROOM_ATTENDING_CEILING: Final[int] = 60
# Below this many remaining places, a listing is worth flagging as about to close.
NEAR_CAPACITY_SPOTS_CEILING: Final[int] = 25
# Travel time to an event is itself a reason not to go, so anything past this is dropped.
DEFAULT_MAX_DISTANCE_MILES: Final[float] = 12.0
# An event within this margin of an existing commitment is treated as clashing with it.
COMMITMENT_CLASH_MARGIN: Final[timedelta] = timedelta(hours=2)
# Nobody is continuously busy for longer than this, so a longer entry marks a span
# (a trip, a term of classes) rather than an appointment that blocks an evening.
MAX_BLOCKING_COMMITMENT_DURATION: Final[timedelta] = timedelta(hours=12)
# Two listings are only the same booking if they start at nearly the same moment.
SAME_EVENT_TIME_TOLERANCE: Final[timedelta] = timedelta(hours=2)
# How much of two titles must overlap before they are treated as the same event.
SAME_EVENT_TITLE_SIMILARITY: Final[float] = 0.6

# Everything that can never be evidence of a shared interest or a shared identity.
UNINFORMATIVE_WORDS: Final[frozenset[str]] = STOP_WORDS | PLACE_WORDS | TIMING_WORDS


@pure
def extract_words(text: str) -> tuple[str, ...]:
    return tuple(WORD_PATTERN.findall(text.lower()))


@pure
def derive_interest_keywords(
    commitment_titles: Sequence[str],
    minimum_occurrence_count: int,
    maximum_keyword_count: int,
) -> tuple[InterestKeyword, ...]:
    """Keep the words that recur across calendar entries, once work and admin words are dropped."""
    word_counts: Counter[str] = Counter()
    for title in commitment_titles:
        # Count each word once per entry so a single verbose title cannot dominate.
        for word in set(extract_words(title)):
            if (
                word in UNINFORMATIVE_WORDS
                or word in NON_INTEREST_TOKENS
                or len(word) < 3
                or word.isdigit()
            ):
                continue
            word_counts[word] += 1

    # A word in a large share of all entries describes the calendar's owner rather than
    # an interest -- their own name, their employer -- and would match everything.
    ubiquity_ceiling = max(
        minimum_occurrence_count, int(len(commitment_titles) * UBIQUITOUS_WORD_SHARE)
    )
    repeated_words = [
        word
        for word, count in word_counts.most_common()
        if minimum_occurrence_count <= count <= ubiquity_ceiling
    ]
    return tuple(
        InterestKeyword(word) for word in repeated_words[:maximum_keyword_count]
    )


@pure
def calculate_distance_miles(origin: GeoPoint, destination: GeoPoint) -> MilesFromHome:
    origin_latitude_radians = math.radians(origin.latitude)
    destination_latitude_radians = math.radians(destination.latitude)
    latitude_delta_radians = math.radians(destination.latitude - origin.latitude)
    longitude_delta_radians = math.radians(destination.longitude - origin.longitude)

    haversine_term = (
        math.sin(latitude_delta_radians / 2) ** 2
        + math.cos(origin_latitude_radians)
        * math.cos(destination_latitude_radians)
        * math.sin(longitude_delta_radians / 2) ** 2
    )
    central_angle = 2 * math.asin(math.sqrt(haversine_term))
    return MilesFromHome(EARTH_RADIUS_MILES * central_angle)


@pure
def is_recurring_series_title(title: str) -> bool:
    return RECURRING_TITLE_PATTERN.search(title) is not None


@pure
def find_matched_keywords(
    event: LocalEvent,
    interest_keywords: Sequence[InterestKeyword],
) -> tuple[InterestKeyword, ...]:
    searchable_words = set(
        extract_words(f"{event.title} {event.description} {event.location.venue_name}")
    )
    return tuple(
        keyword for keyword in interest_keywords if keyword in searchable_words
    )


@pure
def _is_same_event_as_commitment(
    event: LocalEvent, commitment: CalendarCommitment
) -> bool:
    # The same event booked twice starts at the same time, so require that first:
    # title overlap alone matches any two events that share a city name.
    if (
        abs((event.starts_at - commitment.starts_at).total_seconds())
        > SAME_EVENT_TIME_TOLERANCE.total_seconds()
    ):
        return False
    return (
        _calculate_title_similarity(event.title, commitment.title)
        >= SAME_EVENT_TITLE_SIMILARITY
    )


@pure
def _calculate_title_similarity(first_title: str, second_title: str) -> float:
    """How much two titles overlap, ignoring filler and the place names everything shares."""
    first_words = set(extract_words(first_title)) - UNINFORMATIVE_WORDS
    second_words = set(extract_words(second_title)) - UNINFORMATIVE_WORDS
    if not first_words or not second_words:
        return 0.0
    return len(first_words & second_words) / len(first_words | second_words)


@pure
def _clashes_with_commitment(event: LocalEvent, commitment: CalendarCommitment) -> bool:
    # A whole-date marker and a multi-week course entry both describe a period rather
    # than an occupied evening. Subscribed calendars are full of the latter -- a term
    # of weekly classes published as one entry spanning months -- and treating either
    # as a clash blanks out the entire window.
    if (
        commitment.is_all_day
        or (commitment.ends_at - commitment.starts_at)
        > MAX_BLOCKING_COMMITMENT_DURATION
    ):
        return False
    return (
        commitment.starts_at - COMMITMENT_CLASH_MARGIN
        <= event.starts_at
        <= commitment.ends_at + COMMITMENT_CLASH_MARGIN
    )


@pure
def _find_rejection_reason(
    event: LocalEvent,
    profile: UserProfile,
    window_start: datetime,
    window_end: datetime,
    max_distance_miles: float,
    declined_source_event_ids: frozenset[str],
) -> RejectionReason | None:
    if event.source_event_id in declined_source_event_ids:
        return RejectionReason.PREVIOUSLY_DECLINED
    if not window_start <= event.starts_at <= window_end:
        return RejectionReason.OUTSIDE_TIME_WINDOW
    if event.cost.is_sold_out:
        return RejectionReason.SOLD_OUT

    distance_miles = _calculate_event_distance(event, profile)
    if distance_miles is not None and distance_miles > max_distance_miles:
        return RejectionReason.TOO_FAR

    for commitment in profile.commitments:
        if _is_same_event_as_commitment(event, commitment):
            return RejectionReason.ALREADY_COMMITTED
    for commitment in profile.commitments:
        if _clashes_with_commitment(event, commitment):
            return RejectionReason.CLASHES_WITH_COMMITMENT

    # An event must connect to something the user actually does. Without this gate the
    # structural bonuses alone carry an event, and the events that are reliably free,
    # small and recurring are tech meetups -- so the tool recommends more of exactly
    # what it should be filtering out.
    if not find_matched_keywords(event, profile.interest_keywords):
        return RejectionReason.NO_INTEREST_MATCH
    return None


@pure
def _calculate_event_distance(
    event: LocalEvent, profile: UserProfile
) -> MilesFromHome | None:
    if profile.home_coordinate is None or event.location.coordinate is None:
        return None
    return calculate_distance_miles(profile.home_coordinate, event.location.coordinate)


@pure
def score_event(event: LocalEvent, profile: UserProfile) -> ScoredEvent:
    matched_keywords = find_matched_keywords(event, profile.interest_keywords)
    distance_miles = _calculate_event_distance(event, profile)

    # Build the score and the human-readable justification together, so every point
    # added to the total has a sentence the user can read next to it.
    running_score = KEYWORD_MATCH_WEIGHT * len(matched_keywords)
    reasons: list[str] = []
    if matched_keywords:
        reasons.append(f"Matches what you already do: {', '.join(matched_keywords)}")
    if event.is_recurring_series:
        running_score += RECURRING_SERIES_BONUS
        reasons.append("Runs regularly, so you would see the same people again")
    if (
        event.attending_count is not None
        and event.attending_count <= SMALL_ROOM_ATTENDING_CEILING
    ):
        running_score += SMALL_ROOM_BONUS
        reasons.append("Small enough room that you would actually get talked to")
    if event.cost.is_free:
        running_score += FREE_EVENT_BONUS
        reasons.append("Free")
    if (
        event.cost.spots_remaining is not None
        and event.cost.spots_remaining <= NEAR_CAPACITY_SPOTS_CEILING
    ):
        running_score += NEAR_CAPACITY_BONUS
        reasons.append(f"Only {event.cost.spots_remaining} places left")
    if distance_miles is not None:
        running_score -= DISTANCE_PENALTY_PER_MILE * distance_miles
        reasons.append(f"About {distance_miles:.0f} miles away")

    return ScoredEvent(
        event=event,
        score=MatchScore(max(0.0, running_score)),
        matched_keywords=matched_keywords,
        distance_miles=distance_miles,
        reasons=tuple(reasons),
    )


@pure
def select_events_to_surface(
    events: Sequence[LocalEvent],
    profile: UserProfile,
    window_start: datetime,
    window_end: datetime,
    max_distance_miles: float,
    max_selected_count: int,
    declined_source_event_ids: frozenset[str],
) -> EventSelection:
    """Filter out what the user cannot or should not be offered, then rank what remains."""
    rejected: list[RejectedEvent] = []
    survivors: list[LocalEvent] = []
    for event in events:
        rejection_reason = _find_rejection_reason(
            event=event,
            profile=profile,
            window_start=window_start,
            window_end=window_end,
            max_distance_miles=max_distance_miles,
            declined_source_event_ids=declined_source_event_ids,
        )
        if rejection_reason is None:
            survivors.append(event)
        else:
            rejected.append(RejectedEvent(event=event, reason=rejection_reason))

    scored_survivors = sorted(
        (score_event(event, profile) for event in survivors),
        key=lambda scored: (-scored.score, scored.event.starts_at),
    )
    return EventSelection(
        selected=tuple(scored_survivors[:max_selected_count]),
        rejected=tuple(rejected),
    )


@pure
def deduplicate_events(events: Sequence[LocalEvent]) -> tuple[LocalEvent, ...]:
    """Collapse the same event appearing on more than one platform into a single listing."""
    kept_events: list[LocalEvent] = []
    for event in events:
        duplicate_index = next(
            (
                index
                for index, kept in enumerate(kept_events)
                if _is_same_listing(kept, event)
            ),
            None,
        )
        if duplicate_index is None:
            kept_events.append(event)
        elif _count_known_fields(event) > _count_known_fields(
            kept_events[duplicate_index]
        ):
            # Keep whichever platform disclosed more about the same event.
            kept_events[duplicate_index] = event
        else:
            pass
    return tuple(kept_events)


@pure
def _is_same_listing(first: LocalEvent, second: LocalEvent) -> bool:
    if (
        abs((first.starts_at - second.starts_at).total_seconds())
        > SAME_EVENT_TIME_TOLERANCE.total_seconds()
    ):
        return False
    return (
        _calculate_title_similarity(first.title, second.title)
        >= SAME_EVENT_TITLE_SIMILARITY
    )


@pure
def _count_known_fields(event: LocalEvent) -> int:
    return sum(
        1
        for value in (
            event.description,
            event.host_name,
            event.location.venue_name,
            event.location.neighborhood,
            event.location.coordinate,
            event.attending_count,
        )
        if value
    )
