import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from event_ranking import is_recurring_series_title
from event_types import (
    EventCost,
    EventLocation,
    EventSourceError,
    EventSourceName,
    EventTitle,
    GeoPoint,
    LocalEvent,
)
from imbue.imbue_common.primitives import NonEmptyStr, NonNegativeInt
from imbue.imbue_common.pure import pure
from loguru import logger

BROWSER_USER_AGENT: Final[str] = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

PARTIFUL_EXPLORE_URL: Final[str] = "https://partiful.com/explore"
PARTIFUL_EVENT_URL_PREFIX: Final[str] = "https://partiful.com/e/"
LUMA_DISCOVERY_URL: Final[str] = "https://api.lu.ma/discover/get-paginated-events"
LUMA_EVENT_URL_PREFIX: Final[str] = "https://lu.ma/"

# Partiful serves its city feeds from a Next.js data route keyed by a build id that
# changes on every one of their deploys, so it is read from the live page each run
# rather than stored. Partiful publishes city feeds for these regions only.
PARTIFUL_REGIONS: Final[tuple[str, ...]] = ("sf", "nyc", "la", "dc")

NEXT_DATA_PATTERN: Final[re.Pattern[str]] = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL
)
# Partiful does not publish venue coordinates as a field, but it embeds them in the
# Apple Maps link it builds for each venue.
APPLE_MAPS_COORDINATE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"sll=(-?\d+\.?\d*),(-?\d+\.?\d*)"
)

NETWORK_TIMEOUT_SECONDS: Final[float] = 30.0
SLOW_REQUEST_WARNING_SECONDS: Final[float] = 15.0
LUMA_PAGE_SIZE: Final[int] = 50
LUMA_MAX_PAGE_COUNT: Final[int] = 6
LUMA_PAGE_DELAY_SECONDS: Final[float] = 0.4


def _read_url_text(url: str) -> str:
    """Raises EventSourceError when the source cannot be reached or returns an error."""
    request = urllib.request.Request(
        url, headers={"accept": "*/*", "user-agent": BROWSER_USER_AGENT}
    )
    started_at = time.monotonic()
    try:
        with urllib.request.urlopen(
            request, timeout=NETWORK_TIMEOUT_SECONDS
        ) as response:
            body = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise EventSourceError(f"Cannot read event source: {url}") from e
    elapsed_seconds = time.monotonic() - started_at
    if elapsed_seconds > SLOW_REQUEST_WARNING_SECONDS:
        logger.warning("Read {} slowly ({:.1f}s)", url, elapsed_seconds)
    return body


def _read_url_json(url: str) -> Any:
    """Raises EventSourceError when the response is not the JSON the source promised."""
    body = _read_url_text(url)
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise EventSourceError(f"Event source returned non-JSON: {url}") from e


@pure
def _parse_utc_timestamp(raw_timestamp: str) -> datetime:
    return datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00")).astimezone(
        timezone.utc
    )


@pure
def _extract_apple_maps_coordinate(apple_maps_url: str) -> GeoPoint | None:
    match = APPLE_MAPS_COORDINATE_PATTERN.search(apple_maps_url)
    if match is None:
        return None
    return GeoPoint(latitude=float(match.group(1)), longitude=float(match.group(2)))


def read_partiful_build_id() -> str:
    """Raises EventSourceError when Partiful's explore page cannot be parsed."""
    page_html = _read_url_text(PARTIFUL_EXPLORE_URL)
    match = NEXT_DATA_PATTERN.search(page_html)
    if match is None:
        raise EventSourceError(
            "Partiful's explore page no longer embeds the data block this reads"
        )
    try:
        return json.loads(match.group(1))["buildId"]
    except (json.JSONDecodeError, KeyError) as e:
        raise EventSourceError(
            "Partiful's explore page data block has an unexpected shape"
        ) from e


@pure
def _convert_partiful_event(raw_event: Mapping[str, Any]) -> LocalEvent | None:
    title = (raw_event.get("title") or "").strip()
    event_id = raw_event.get("id")
    started_at_raw = raw_event.get("startDate")
    if not title or not event_id or not started_at_raw:
        return None

    maps_info = (raw_event.get("locationInfo") or {}).get("mapsInfo") or {}
    approximate_location = maps_info.get("approximateLocation") or ""
    location = EventLocation(
        venue_name=maps_info.get("name") or "",
        locality=approximate_location.split(",")[0].strip(),
        neighborhood=(raw_event.get("locationInfo") or {}).get("neighborhood") or "",
        coordinate=_extract_apple_maps_coordinate(maps_info.get("appleMapsUrl") or ""),
    )
    going_count = raw_event.get("goingGuestCount")
    return LocalEvent(
        source=EventSourceName.PARTIFUL,
        source_event_id=NonEmptyStr(event_id),
        source_url=NonEmptyStr(f"{PARTIFUL_EVENT_URL_PREFIX}{event_id}"),
        title=EventTitle(title),
        description=(raw_event.get("description") or "").strip(),
        host_name=raw_event.get("hostName") or "",
        starts_at=_parse_utc_timestamp(started_at_raw),
        location=location,
        # Partiful does not publish ticket prices in its city feeds, so cost is only
        # ever known to be unstated here -- never assume free.
        cost=EventCost(
            is_free=False, price_cents=None, is_sold_out=False, spots_remaining=None
        ),
        attending_count=None if going_count is None else NonNegativeInt(going_count),
        is_recurring_series=is_recurring_series_title(title),
    )


@pure
def _collect_partiful_raw_events(
    page_props: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    # Partiful splits the same city feed across a trending carousel, themed sections,
    # and a flat feed, with the same event often appearing in several of them.
    raw_event_by_id: dict[str, Mapping[str, Any]] = {}
    section_groups: list[Any] = list(page_props.get("sections") or [])
    trending_section = page_props.get("trendingSection")
    if trending_section:
        section_groups.append(trending_section)
    for section in section_groups:
        for item in section.get("items") or []:
            raw_event = item.get("event") or {}
            if raw_event.get("id"):
                raw_event_by_id[raw_event["id"]] = raw_event
    for item in page_props.get("feedItems") or []:
        raw_event = item.get("event") or {}
        if raw_event.get("id"):
            raw_event_by_id[raw_event["id"]] = raw_event
    return tuple(raw_event_by_id.values())


def fetch_partiful_events(region: str, raw_output_dir: Path) -> tuple[LocalEvent, ...]:
    """Raises EventSourceError when Partiful cannot be read or does not cover the region."""
    if region not in PARTIFUL_REGIONS:
        raise EventSourceError(
            f"Partiful publishes no city feed for '{region}' (it covers {PARTIFUL_REGIONS})"
        )

    build_id = read_partiful_build_id()
    feed_url = f"https://partiful.com/_next/data/{build_id}/explore/{region}.json"
    payload = _read_url_json(feed_url)
    _write_raw_payload(raw_output_dir, EventSourceName.PARTIFUL, region, payload)

    page_props = payload.get("pageProps") or {}
    if not page_props:
        raise EventSourceError(f"Partiful returned an empty feed for region '{region}'")

    converted = (
        _convert_partiful_event(raw_event)
        for raw_event in _collect_partiful_raw_events(page_props)
    )
    return tuple(event for event in converted if event is not None)


@pure
def _convert_luma_entry(entry: Mapping[str, Any]) -> LocalEvent | None:
    raw_event = entry.get("event") or {}
    title = (raw_event.get("name") or "").strip()
    event_id = raw_event.get("api_id")
    started_at_raw = raw_event.get("start_at")
    event_slug = raw_event.get("url")
    if not title or not event_id or not started_at_raw or not event_slug:
        return None

    geo_address = raw_event.get("geo_address_info") or {}
    raw_coordinate = raw_event.get("coordinate") or {}
    coordinate = (
        GeoPoint(
            latitude=raw_coordinate["latitude"], longitude=raw_coordinate["longitude"]
        )
        if "latitude" in raw_coordinate and "longitude" in raw_coordinate
        else None
    )
    location = EventLocation(
        venue_name=geo_address.get("address") or "",
        locality=geo_address.get("city") or "",
        neighborhood=geo_address.get("sublocality") or "",
        coordinate=coordinate,
    )

    ticket_info = entry.get("ticket_info") or {}
    raw_price = ticket_info.get("price") or {}
    spots_remaining = ticket_info.get("spots_remaining")
    cost = EventCost(
        is_free=bool(ticket_info.get("is_free")),
        price_cents=NonNegativeInt(raw_price["cents"])
        if raw_price.get("cents") is not None
        else None,
        is_sold_out=bool(ticket_info.get("is_sold_out")),
        spots_remaining=None
        if spots_remaining is None
        else NonNegativeInt(spots_remaining),
    )

    calendar = entry.get("calendar") or {}
    guest_count = entry.get("guest_count")
    hosts = entry.get("hosts") or []
    return LocalEvent(
        source=EventSourceName.LUMA,
        source_event_id=NonEmptyStr(event_id),
        source_url=NonEmptyStr(f"{LUMA_EVENT_URL_PREFIX}{event_slug}"),
        title=EventTitle(title),
        description=(calendar.get("description_short") or "").strip(),
        host_name=(hosts[0].get("name") if hosts else calendar.get("name")) or "",
        starts_at=_parse_utc_timestamp(started_at_raw),
        location=location,
        cost=cost,
        attending_count=None if guest_count is None else NonNegativeInt(guest_count),
        # Luma models a repeating event as a recurrence group, which is a stronger
        # signal than anything the title says.
        is_recurring_series=raw_event.get("recurrence_id") is not None
        or is_recurring_series_title(title),
    )


def fetch_luma_events(city_slug: str, raw_output_dir: Path) -> tuple[LocalEvent, ...]:
    """Raises EventSourceError when Luma cannot be read."""
    collected_entries: list[Mapping[str, Any]] = []
    pagination_cursor: str | None = None
    for _ in range(LUMA_MAX_PAGE_COUNT):
        query = {
            "period": "future",
            "pagination_limit": str(LUMA_PAGE_SIZE),
            "slug": city_slug,
        }
        if pagination_cursor:
            query["pagination_cursor"] = pagination_cursor
        payload = _read_url_json(
            f"{LUMA_DISCOVERY_URL}?{urllib.parse.urlencode(query)}"
        )
        collected_entries.extend(payload.get("entries") or [])
        pagination_cursor = payload.get("next_cursor")
        if not payload.get("has_more") or not pagination_cursor:
            break
        time.sleep(LUMA_PAGE_DELAY_SECONDS)

    _write_raw_payload(
        raw_output_dir, EventSourceName.LUMA, city_slug, collected_entries
    )
    converted = (_convert_luma_entry(entry) for entry in collected_entries)
    return tuple(event for event in converted if event is not None)


def _write_raw_payload(
    raw_output_dir: Path,
    source: EventSourceName,
    region: str,
    payload: Any,
) -> Path:
    """Keep every source response on disk so a change in processing never needs a refetch."""
    raw_output_dir.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw_path = raw_output_dir / f"{source.lower()}-{region}-{fetched_at}.json"
    raw_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    return raw_path


def fetch_all_events(region: str, raw_output_dir: Path) -> tuple[LocalEvent, ...]:
    """Read every source that covers the region, tolerating one of them being down."""
    collected_events: list[LocalEvent] = []
    for source_name, fetch in (
        (
            EventSourceName.PARTIFUL,
            lambda: fetch_partiful_events(region, raw_output_dir),
        ),
        (EventSourceName.LUMA, lambda: fetch_luma_events(region, raw_output_dir)),
    ):
        try:
            collected_events.extend(fetch())
        except EventSourceError as e:
            logger.warning("Skipped {} for region {}: {}", source_name, region, e)
    if not collected_events:
        raise EventSourceError(f"No source returned any events for region '{region}'")
    return tuple(collected_events)


def write_events_jsonl(events: Sequence[LocalEvent], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "\n".join(event.model_dump_json() for event in events) + "\n"
    )
