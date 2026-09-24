import argparse
import json
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

from calendar_profile import build_profile_from_calendar
from event_ranking import (
    DEFAULT_MAX_DISTANCE_MILES,
    deduplicate_events,
    select_events_to_surface,
)
from event_sources import fetch_all_events, write_events_jsonl
from event_types import (
    EventSelection,
    GeoPoint,
    LocalEventsError,
    ScoredEvent,
    UserProfile,
)
from imbue.imbue_common.logging import setup_logging
from imbue.imbue_common.pure import pure
from loguru import logger

# Anchor state to the repo root rather than the working directory: this runs both from
# the repo root (scheduled) and from the scripts directory (by hand).
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[4]
SKILL_STATE_DIR: Final[Path] = REPO_ROOT / "data/.skills/local-events-finder"
RAW_EVENTS_DIR: Final[Path] = SKILL_STATE_DIR / "raw"
NORMALIZED_EVENTS_PATH: Final[Path] = SKILL_STATE_DIR / "events.jsonl"
DECISIONS_PATH: Final[Path] = SKILL_STATE_DIR / "decisions.jsonl"
PROFILE_PATH: Final[Path] = SKILL_STATE_DIR / "profile.json"

DEFAULT_WINDOW_DAYS: Final[int] = 10
DEFAULT_SELECTED_COUNT: Final[int] = 3


def read_declined_source_event_ids(decisions_path: Path) -> frozenset[str]:
    """Read back every event the user has already turned down, so it is never offered twice."""
    if not decisions_path.exists():
        return frozenset()
    declined_ids: set[str] = set()
    for line in decisions_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            decision = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("Skipped an unreadable line in the decision history")
            continue
        if decision.get("verdict") == "no" and decision.get("source_event_id"):
            declined_ids.add(decision["source_event_id"])
    return frozenset(declined_ids)


def record_decision(decisions_path: Path, source_event_id: str, verdict: str) -> None:
    decisions_path.parent.mkdir(parents=True, exist_ok=True)
    decision_record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source_event_id": source_event_id,
        "verdict": verdict,
    }
    with decisions_path.open("a") as decisions_file:
        decisions_file.write(json.dumps(decision_record) + "\n")


def format_selection_for_reading(selection: EventSelection, timezone_name: str) -> str:
    """Render the picks the way they should be read out, not as a data dump."""
    if not selection.selected:
        return "Nothing worth going to turned up in this window."
    display_timezone = ZoneInfo(timezone_name)
    lines: list[str] = []
    for scored_event in selection.selected:
        lines.append(_format_scored_event(scored_event, display_timezone))
    return "\n\n".join(lines)


def _format_scored_event(scored_event: ScoredEvent, display_timezone: ZoneInfo) -> str:
    event = scored_event.event
    local_start = event.starts_at.astimezone(display_timezone)
    when = (
        local_start.strftime("%a %b %-d, %-I:%M%p")
        .replace("AM", "am")
        .replace("PM", "pm")
    )
    where = (
        event.location.venue_name
        or event.location.neighborhood
        or event.location.locality
    )
    cost = (
        "free"
        if event.cost.is_free
        else (f"${event.cost.price_cents / 100:.0f}" if event.cost.price_cents else "")
    )
    header_parts = [part for part in (when, where, cost) if part]
    reason_lines = "\n".join(f"  - {reason}" for reason in scored_event.reasons)
    return f"{event.title}\n  {' | '.join(header_parts)}\n  {event.source_url}\n{reason_lines}"


def run_event_search(
    region: str,
    window_days: int,
    max_distance_miles: float,
    max_selected_count: int,
    home_coordinate: GeoPoint | None,
) -> tuple[EventSelection, UserProfile]:
    profile = build_profile_from_calendar(home_coordinate=home_coordinate)
    logger.info(
        "Read the calendar (home={}, {} interests)",
        profile.home_locality,
        len(profile.interest_keywords),
    )

    fetched_events = fetch_all_events(region=region, raw_output_dir=RAW_EVENTS_DIR)
    unique_events = deduplicate_events(fetched_events)
    write_events_jsonl(unique_events, NORMALIZED_EVENTS_PATH)
    logger.info(
        "Collected {} events ({} after removing cross-platform repeats)",
        len(fetched_events),
        len(unique_events),
    )

    window_start = datetime.now(timezone.utc)
    selection = select_events_to_surface(
        events=unique_events,
        profile=profile,
        window_start=window_start,
        window_end=window_start + timedelta(days=window_days),
        max_distance_miles=max_distance_miles,
        max_selected_count=max_selected_count,
        declined_source_event_ids=read_declined_source_event_ids(DECISIONS_PATH),
    )
    _write_profile_snapshot(profile)
    return selection, profile


def _write_profile_snapshot(profile: UserProfile) -> None:
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(profile.model_dump_json(indent=1))


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Find local events worth going to, based on your own calendar."
    )
    parser.add_argument(
        "--region", default="sf", help="City feed to read (sf, nyc, la, dc)"
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=DEFAULT_WINDOW_DAYS,
        help="How far ahead to look",
    )
    parser.add_argument(
        "--max-distance-miles",
        type=float,
        default=DEFAULT_MAX_DISTANCE_MILES,
        help="Drop anything further than this from home",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=DEFAULT_SELECTED_COUNT,
        help="How many events to surface",
    )
    parser.add_argument(
        "--home-latitude",
        type=float,
        default=None,
        help="Home latitude, for the distance filter",
    )
    parser.add_argument(
        "--home-longitude",
        type=float,
        default=None,
        help="Home longitude, for the distance filter",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full selection as JSON instead of prose",
    )
    return parser


@pure
def _resolve_home_coordinate(
    latitude: float | None, longitude: float | None
) -> GeoPoint | None:
    if latitude is None or longitude is None:
        return None
    return GeoPoint(latitude=latitude, longitude=longitude)


def main(argv: Sequence[str] | None = None) -> int:
    setup_logging(level="INFO")
    arguments = _build_argument_parser().parse_args(argv)
    try:
        selection, profile = run_event_search(
            region=arguments.region,
            window_days=arguments.window_days,
            max_distance_miles=arguments.max_distance_miles,
            max_selected_count=arguments.count,
            home_coordinate=_resolve_home_coordinate(
                arguments.home_latitude, arguments.home_longitude
            ),
        )
    except LocalEventsError as e:
        logger.error("Could not find events: {}", e)
        return 1

    if arguments.json:
        print(selection.model_dump_json(indent=1))
    else:
        print(format_selection_for_reading(selection, profile.timezone_name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
