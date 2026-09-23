import json
from datetime import timezone
from pathlib import Path

from event_sources import (
    _collect_partiful_raw_events,
    _convert_luma_entry,
    _convert_partiful_event,
    _extract_apple_maps_coordinate,
    write_events_jsonl,
)
from event_types import EventSourceName

PARTIFUL_RAW_EVENT = {
    "id": "niZdDIpgOIp7FKdOSg9j",
    "title": "costco hotdog run",
    "description": "Work up your appetite on a 5k through the Embarcadero.",
    "startDate": "2026-10-03T17:30:00.000Z",
    "goingGuestCount": 211,
    "locationInfo": {
        "mapsInfo": {
            "name": "Ferry Building",
            "approximateLocation": "San Francisco, CA",
            "appleMapsUrl": "http://maps.apple.com/?address=1%20Ferry%20Building&sll=37.80,-122.39",
        },
        "neighborhood": "Financial District",
    },
}

LUMA_RAW_ENTRY = {
    "event": {
        "api_id": "evt-JyKG7U4110cFMjH",
        "name": "Mahjong After Dark",
        "start_at": "2026-09-27T02:00:00.000Z",
        "url": "5cewfkx1",
        "recurrence_id": None,
        "geo_address_info": {"city": "San Francisco", "sublocality": "Mission"},
        "coordinate": {"latitude": 37.7872, "longitude": -122.4013},
    },
    "ticket_info": {
        "is_free": False,
        "price": {"cents": 1500},
        "is_sold_out": False,
        "spots_remaining": 66,
    },
    "calendar": {"name": "mahjong.mami", "description_short": "A small tiles night"},
    "hosts": [{"name": "Mami"}],
    "guest_count": 7,
}


def test_extract_apple_maps_coordinate_reads_the_embedded_pair() -> None:
    coordinate = _extract_apple_maps_coordinate(
        "http://maps.apple.com/?address=x&sll=37.80,-122.39"
    )
    assert coordinate is not None
    assert coordinate.latitude == 37.80
    assert coordinate.longitude == -122.39


def test_extract_apple_maps_coordinate_returns_none_without_one() -> None:
    assert (
        _extract_apple_maps_coordinate(
            "https://www.google.com/maps/search/?api=1&query=x"
        )
        is None
    )


def test_convert_partiful_event_reads_every_published_field() -> None:
    event = _convert_partiful_event(PARTIFUL_RAW_EVENT)
    assert event is not None
    assert event.source == EventSourceName.PARTIFUL
    assert event.title == "costco hotdog run"
    assert event.source_url == "https://partiful.com/e/niZdDIpgOIp7FKdOSg9j"
    assert event.starts_at.tzinfo == timezone.utc
    assert event.location.locality == "San Francisco"
    assert event.location.neighborhood == "Financial District"
    assert event.location.coordinate is not None
    assert event.attending_count == 211


def test_convert_partiful_event_never_claims_an_unstated_price_is_free() -> None:
    event = _convert_partiful_event(PARTIFUL_RAW_EVENT)
    assert event is not None
    assert not event.cost.is_free
    assert event.cost.price_cents is None


def test_convert_partiful_event_skips_a_listing_missing_its_essentials() -> None:
    assert (
        _convert_partiful_event(
            {"id": "x", "title": "", "startDate": "2026-10-03T17:30:00.000Z"}
        )
        is None
    )
    assert _convert_partiful_event({"id": "x", "title": "Something"}) is None


def test_collect_partiful_raw_events_merges_sections_without_repeating_events() -> None:
    page_props = {
        "trendingSection": {"items": [{"event": {"id": "a"}}]},
        "sections": [{"items": [{"event": {"id": "a"}}, {"event": {"id": "b"}}]}],
        "feedItems": [{"event": {"id": "c"}}],
    }
    collected_ids = {
        raw_event["id"] for raw_event in _collect_partiful_raw_events(page_props)
    }
    assert collected_ids == {"a", "b", "c"}


def test_convert_luma_entry_reads_price_and_availability() -> None:
    event = _convert_luma_entry(LUMA_RAW_ENTRY)
    assert event is not None
    assert event.source == EventSourceName.LUMA
    assert event.source_url == "https://lu.ma/5cewfkx1"
    assert not event.cost.is_free
    assert event.cost.price_cents == 1500
    assert event.cost.spots_remaining == 66
    assert event.host_name == "Mami"
    assert event.location.neighborhood == "Mission"


def test_convert_luma_entry_treats_a_recurrence_group_as_a_series() -> None:
    recurring_entry = json.loads(json.dumps(LUMA_RAW_ENTRY))
    recurring_entry["event"]["recurrence_id"] = "rec-1"
    event = _convert_luma_entry(recurring_entry)
    assert event is not None
    assert event.is_recurring_series


def test_convert_luma_entry_skips_a_listing_without_a_link() -> None:
    entry_without_slug = json.loads(json.dumps(LUMA_RAW_ENTRY))
    del entry_without_slug["event"]["url"]
    assert _convert_luma_entry(entry_without_slug) is None


def test_write_events_jsonl_round_trips_every_event(tmp_path: Path) -> None:
    events = tuple(
        event
        for event in (
            _convert_partiful_event(PARTIFUL_RAW_EVENT),
            _convert_luma_entry(LUMA_RAW_ENTRY),
        )
        if event is not None
    )
    output_path = tmp_path / "events.jsonl"
    write_events_jsonl(events, output_path)
    written_lines = [
        line for line in output_path.read_text().splitlines() if line.strip()
    ]
    assert len(written_lines) == 2
    assert {json.loads(line)["source"] for line in written_lines} == {
        "PARTIFUL",
        "LUMA",
    }
