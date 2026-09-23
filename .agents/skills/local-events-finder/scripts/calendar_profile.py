import json
import subprocess
import urllib.parse
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any, Final

from event_ranking import derive_interest_keywords
from event_types import CalendarCommitment, CalendarReadError, GeoPoint, UserProfile
from imbue.imbue_common.primitives import NonEmptyStr
from imbue.imbue_common.pure import pure
from loguru import logger

GOOGLE_CALENDAR_API_ROOT: Final[str] = "https://www.googleapis.com/calendar/v3"
LATCHKEY_TIMEOUT_SECONDS: Final[float] = 60.0

# How far back to read the calendar when working out what the user actually does.
INTEREST_LOOKBACK: Final[timedelta] = timedelta(days=120)
# How far forward to read it when working out what they are already committed to.
COMMITMENT_LOOKAHEAD: Final[timedelta] = timedelta(days=60)

# A word must appear in this many separate calendar entries before it counts as an interest.
MINIMUM_INTEREST_OCCURRENCE_COUNT: Final[int] = 2
MAXIMUM_INTEREST_KEYWORD_COUNT: Final[int] = 30
CALENDAR_PAGE_SIZE: Final[int] = 250
# Below this many entries there is not enough evidence to judge a calendar's variety.
MINIMUM_ENTRIES_TO_JUDGE_A_CALENDAR: Final[int] = 40
# A calendar whose distinct titles are a smaller share of its entries than this is
# repeating a fixed vocabulary, which is what a tracker app does and a person does not.
MACHINE_GENERATED_TITLE_VARIETY: Final[float] = 0.2
# An all-day entry has no clock time; treat it as filling its whole day.
ALL_DAY_DURATION: Final[timedelta] = timedelta(days=1)


def _run_latchkey_curl(url: str) -> Any:
    """Raises CalendarReadError when the calendar cannot be reached or does not return JSON."""
    try:
        completed = subprocess.run(
            ["latchkey", "curl", "-sf", url],
            capture_output=True,
            text=True,
            timeout=LATCHKEY_TIMEOUT_SECONDS,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        raise CalendarReadError(f"Calendar request was refused: {url}") from e
    except subprocess.TimeoutExpired as e:
        raise CalendarReadError(f"Calendar request timed out: {url}") from e
    except FileNotFoundError as e:
        raise CalendarReadError(
            "The latchkey command is not available in this workspace"
        ) from e
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as e:
        raise CalendarReadError(f"Calendar returned non-JSON: {url}") from e


def list_calendar_ids() -> tuple[str, ...]:
    """Raises CalendarReadError when the calendar list cannot be read."""
    payload = _run_latchkey_curl(f"{GOOGLE_CALENDAR_API_ROOT}/users/me/calendarList")
    items = payload.get("items")
    if not items:
        raise CalendarReadError(
            "No calendars are reachable -- connect Google Calendar first"
        )
    return tuple(item["id"] for item in items if item.get("id"))


def read_primary_timezone() -> str:
    """Raises CalendarReadError when the primary calendar cannot be read."""
    payload = _run_latchkey_curl(f"{GOOGLE_CALENDAR_API_ROOT}/users/me/calendarList")
    for item in payload.get("items") or []:
        if item.get("primary") and item.get("timeZone"):
            return item["timeZone"]
    raise CalendarReadError("No primary calendar with a timezone was found")


def fetch_calendar_entries(
    calendar_id: str,
    window_start: datetime,
    window_end: datetime,
) -> tuple[Mapping[str, Any], ...]:
    """Raises CalendarReadError when this calendar cannot be read."""
    query = urllib.parse.urlencode(
        {
            "timeMin": window_start.isoformat().replace("+00:00", "Z"),
            "timeMax": window_end.isoformat().replace("+00:00", "Z"),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": str(CALENDAR_PAGE_SIZE),
        }
    )
    encoded_calendar_id = urllib.parse.quote(calendar_id)
    payload = _run_latchkey_curl(
        f"{GOOGLE_CALENDAR_API_ROOT}/calendars/{encoded_calendar_id}/events?{query}"
    )
    return tuple(payload.get("items") or [])


@pure
def _parse_calendar_time(raw_time: Mapping[str, Any]) -> datetime | None:
    if "dateTime" in raw_time:
        return datetime.fromisoformat(raw_time["dateTime"]).astimezone(timezone.utc)
    if "date" in raw_time:
        return datetime.fromisoformat(raw_time["date"]).replace(tzinfo=timezone.utc)
    return None


@pure
def convert_calendar_entry(raw_entry: Mapping[str, Any]) -> CalendarCommitment | None:
    title = (raw_entry.get("summary") or "").strip()
    raw_start = raw_entry.get("start") or {}
    starts_at = _parse_calendar_time(raw_start)
    if not title or starts_at is None:
        return None
    # Google marks an all-day entry by giving it a bare date instead of a clock time.
    is_all_day = "dateTime" not in raw_start
    ends_at = _parse_calendar_time(raw_entry.get("end") or {}) or (
        starts_at + ALL_DAY_DURATION
    )
    return CalendarCommitment(
        title=title, starts_at=starts_at, ends_at=ends_at, is_all_day=is_all_day
    )


@pure
def is_machine_generated_calendar(raw_entries: Sequence[Mapping[str, Any]]) -> bool:
    """Spot a tracker app's calendar by its tiny vocabulary repeated across many entries."""
    # A sleep or habit tracker emits the same handful of titles ("melatonin window",
    # "afternoon dip") hundreds of times, and that vocabulary swamps every real
    # interest. Volume alone is not the signal -- a genuinely busy person has a busy
    # calendar, but their entries say different things.
    if len(raw_entries) < MINIMUM_ENTRIES_TO_JUDGE_A_CALENDAR:
        return False
    distinct_titles = {
        (entry.get("summary") or "").strip().lower() for entry in raw_entries
    }
    return len(distinct_titles) / len(raw_entries) < MACHINE_GENERATED_TITLE_VARIETY


@pure
def derive_home_locality(raw_entries: Sequence[Mapping[str, Any]]) -> str | None:
    """Pick the town the user's in-person entries cluster in, from their location fields."""
    locality_counts: Counter[str] = Counter()
    for raw_entry in raw_entries:
        location_text = (raw_entry.get("location") or "").strip()
        # A location that is a meeting link or a phone number says nothing about geography.
        if (
            not location_text
            or location_text.startswith("http")
            or "zoom" in location_text.lower()
        ):
            continue
        address_parts = [
            part.strip() for part in location_text.split(",") if part.strip()
        ]
        # In a postal address the town sits immediately before the state/postcode part.
        if len(address_parts) >= 3:
            locality_counts[address_parts[-3]] += 1
    if not locality_counts:
        return None
    return locality_counts.most_common(1)[0][0]


def build_profile_from_calendar(home_coordinate: GeoPoint | None) -> UserProfile:
    """Raises CalendarReadError when the calendar cannot be read at all."""
    now = datetime.now(timezone.utc)
    timezone_name = read_primary_timezone()

    # Read every calendar the user has, including subscribed ones: a subscribed
    # cultural or club calendar is a far stronger interest signal than anything
    # they would think to state.
    collected_entries: list[Mapping[str, Any]] = []
    for calendar_id in list_calendar_ids():
        try:
            calendar_entries = fetch_calendar_entries(
                calendar_id=calendar_id,
                window_start=now - INTEREST_LOOKBACK,
                window_end=now + COMMITMENT_LOOKAHEAD,
            )
        except CalendarReadError as e:
            logger.warning("Skipped calendar {}: {}", calendar_id, e)
            continue
        if is_machine_generated_calendar(calendar_entries):
            logger.info(
                "Ignored calendar {} as machine-generated ({} entries)",
                calendar_id,
                len(calendar_entries),
            )
            continue
        collected_entries.extend(calendar_entries)

    if not collected_entries:
        raise CalendarReadError("Every calendar was unreadable or empty")

    home_locality = derive_home_locality(collected_entries)
    if home_locality is None:
        raise CalendarReadError(
            "No calendar entry carries a street address, so the home city is unknown"
        )

    converted = (convert_calendar_entry(raw_entry) for raw_entry in collected_entries)
    commitments = tuple(
        commitment for commitment in converted if commitment is not None
    )
    interest_keywords = derive_interest_keywords(
        commitment_titles=[commitment.title for commitment in commitments],
        minimum_occurrence_count=MINIMUM_INTEREST_OCCURRENCE_COUNT,
        maximum_keyword_count=MAXIMUM_INTEREST_KEYWORD_COUNT,
    )
    return UserProfile(
        home_locality=NonEmptyStr(home_locality),
        timezone_name=NonEmptyStr(timezone_name),
        home_coordinate=home_coordinate,
        interest_keywords=interest_keywords,
        commitments=commitments,
    )
