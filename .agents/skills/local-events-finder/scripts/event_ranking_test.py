from datetime import datetime, timedelta, timezone

from event_ranking import (
    calculate_distance_miles,
    deduplicate_events,
    derive_interest_keywords,
    find_matched_keywords,
    is_recurring_series_title,
    score_event,
    select_events_to_surface,
)
from event_types import (
    CalendarCommitment,
    EventCost,
    EventLocation,
    EventSourceName,
    EventTitle,
    GeoPoint,
    InterestKeyword,
    LocalEvent,
    RejectionReason,
    UserProfile,
)
from imbue.imbue_common.primitives import NonEmptyStr, NonNegativeInt

SAN_FRANCISCO = GeoPoint(latitude=37.7749, longitude=-122.4194)
OAKLAND = GeoPoint(latitude=37.8044, longitude=-122.2712)
NEW_YORK = GeoPoint(latitude=40.7128, longitude=-74.0060)
WINDOW_START = datetime(2026, 9, 23, tzinfo=timezone.utc)
WINDOW_END = WINDOW_START + timedelta(days=10)


def build_event(
    title: str,
    starts_at: datetime,
    source: EventSourceName = EventSourceName.PARTIFUL,
    source_event_id: str = "evt-1",
    description: str = "",
    coordinate: GeoPoint | None = SAN_FRANCISCO,
    attending_count: int | None = 20,
    is_free: bool = True,
    is_sold_out: bool = False,
    spots_remaining: int | None = None,
    is_recurring_series: bool = False,
    venue_name: str = "A Venue",
    neighborhood: str = "",
) -> LocalEvent:
    return LocalEvent(
        source=source,
        source_event_id=NonEmptyStr(source_event_id),
        source_url=NonEmptyStr(f"https://example.test/{source_event_id}"),
        title=EventTitle(title),
        description=description,
        host_name="",
        starts_at=starts_at,
        location=EventLocation(
            venue_name=venue_name,
            locality="San Francisco",
            neighborhood=neighborhood,
            coordinate=coordinate,
        ),
        cost=EventCost(
            is_free=is_free,
            price_cents=None,
            is_sold_out=is_sold_out,
            spots_remaining=None
            if spots_remaining is None
            else NonNegativeInt(spots_remaining),
        ),
        attending_count=None
        if attending_count is None
        else NonNegativeInt(attending_count),
        is_recurring_series=is_recurring_series,
    )


def build_profile(
    interest_keywords: tuple[str, ...] = ("mahjong",),
    commitments: tuple[CalendarCommitment, ...] = (),
    home_coordinate: GeoPoint | None = SAN_FRANCISCO,
) -> UserProfile:
    return UserProfile(
        home_locality=NonEmptyStr("San Francisco"),
        timezone_name=NonEmptyStr("America/Los_Angeles"),
        home_coordinate=home_coordinate,
        interest_keywords=tuple(
            InterestKeyword(keyword) for keyword in interest_keywords
        ),
        commitments=commitments,
    )


def test_derive_interest_keywords_keeps_words_repeated_across_entries() -> None:
    keywords = derive_interest_keywords(
        commitment_titles=[
            "Preply lesson - Rina K.",
            "Preply lesson - Rina K.",
            "Volleys and Vibes SZN 6",
            "Dentist",
        ],
        minimum_occurrence_count=2,
        maximum_keyword_count=10,
    )
    assert "preply" in keywords
    assert "lesson" in keywords
    assert "dentist" not in keywords


def test_derive_interest_keywords_drops_a_word_present_in_most_entries() -> None:
    keywords = derive_interest_keywords(
        commitment_titles=[
            "Vito and Dana",
            "Vito and Sam",
            "Vito and Priya",
            "Vito mahjong night",
            "Mahjong night",
        ],
        minimum_occurrence_count=2,
        maximum_keyword_count=10,
    )
    assert "vito" not in keywords
    assert "mahjong" in keywords


def test_select_events_rejects_an_event_matching_nothing_the_user_does() -> None:
    unrelated_event = build_event(
        "Series A Pitch Night Vol. 3",
        WINDOW_START + timedelta(days=1),
        is_recurring_series=True,
        attending_count=15,
        is_free=True,
    )
    selection = select_events_to_surface(
        events=[unrelated_event],
        profile=build_profile(interest_keywords=("mahjong",)),
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        max_distance_miles=12.0,
        max_selected_count=3,
        declined_source_event_ids=frozenset(),
    )
    assert not selection.selected
    assert selection.rejected[0].reason == RejectionReason.NO_INTEREST_MATCH


def test_derive_interest_keywords_drops_work_and_admin_words() -> None:
    keywords = derive_interest_keywords(
        commitment_titles=[
            "Interview with Acme",
            "Interview with Globex",
            "Phone call with Initech",
            "Phone call with Umbrella",
        ],
        minimum_occurrence_count=2,
        maximum_keyword_count=10,
    )
    assert "interview" not in keywords
    assert "phone" not in keywords
    assert "call" not in keywords


def test_calculate_distance_miles_matches_known_separation() -> None:
    bay_area_distance = calculate_distance_miles(SAN_FRANCISCO, OAKLAND)
    assert 8.0 < bay_area_distance < 12.0
    transcontinental_distance = calculate_distance_miles(SAN_FRANCISCO, NEW_YORK)
    assert 2500.0 < transcontinental_distance < 2600.0


def test_is_recurring_series_title_detects_repeat_markers() -> None:
    assert is_recurring_series_title("Volleys & Vibes: SZN 6 x WEEK 3")
    assert is_recurring_series_title("September Hiking/Book Club")
    assert not is_recurring_series_title("Barbarossa Closing Party")


def test_find_matched_keywords_searches_title_and_description() -> None:
    event = build_event(
        title="Mahjong After Dark",
        starts_at=WINDOW_START + timedelta(days=1),
        description="A relaxed evening of tiles and snacks",
    )
    matched = find_matched_keywords(
        event, (InterestKeyword("mahjong"), InterestKeyword("tiles"))
    )
    assert set(matched) == {"mahjong", "tiles"}


def test_score_event_ranks_recurring_above_one_off_with_equal_interest() -> None:
    recurring_event = build_event(
        title="Mahjong Club Week 4",
        starts_at=WINDOW_START + timedelta(days=1),
        is_recurring_series=True,
    )
    one_off_event = build_event(
        title="Mahjong Gala",
        starts_at=WINDOW_START + timedelta(days=1),
        is_recurring_series=False,
    )
    profile = build_profile()
    assert (
        score_event(recurring_event, profile).score
        > score_event(one_off_event, profile).score
    )


def test_score_event_penalises_distance() -> None:
    near_event = build_event(
        "Mahjong Night", WINDOW_START + timedelta(days=1), coordinate=SAN_FRANCISCO
    )
    far_event = build_event(
        "Mahjong Night", WINDOW_START + timedelta(days=1), coordinate=OAKLAND
    )
    profile = build_profile()
    assert (
        score_event(near_event, profile).score > score_event(far_event, profile).score
    )


def test_score_event_explains_every_point_it_awards() -> None:
    event = build_event(
        title="Mahjong Club Week 4",
        starts_at=WINDOW_START + timedelta(days=1),
        is_recurring_series=True,
        spots_remaining=5,
    )
    scored = score_event(event, build_profile())
    assert scored.reasons
    assert any("Runs regularly" in reason for reason in scored.reasons)
    assert any("5 places left" in reason for reason in scored.reasons)


def test_select_events_rejects_sold_out_events() -> None:
    sold_out_event = build_event(
        "Mahjong Night", WINDOW_START + timedelta(days=1), is_sold_out=True
    )
    selection = select_events_to_surface(
        events=[sold_out_event],
        profile=build_profile(),
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        max_distance_miles=12.0,
        max_selected_count=3,
        declined_source_event_ids=frozenset(),
    )
    assert not selection.selected
    assert selection.rejected[0].reason == RejectionReason.SOLD_OUT


def test_select_events_rejects_events_too_far_away() -> None:
    distant_event = build_event(
        "Mahjong Night", WINDOW_START + timedelta(days=1), coordinate=NEW_YORK
    )
    selection = select_events_to_surface(
        events=[distant_event],
        profile=build_profile(),
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        max_distance_miles=12.0,
        max_selected_count=3,
        declined_source_event_ids=frozenset(),
    )
    assert selection.rejected[0].reason == RejectionReason.TOO_FAR


def test_select_events_rejects_something_already_on_the_calendar() -> None:
    event_start = WINDOW_START + timedelta(days=2)
    already_going = build_event("Barbarossa Closing Party", event_start)
    profile = build_profile(
        commitments=(
            CalendarCommitment(
                title="Barbarossa Closing Party",
                starts_at=event_start,
                ends_at=event_start + timedelta(hours=3),
                is_all_day=False,
            ),
        )
    )
    selection = select_events_to_surface(
        events=[already_going],
        profile=profile,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        max_distance_miles=12.0,
        max_selected_count=3,
        declined_source_event_ids=frozenset(),
    )
    assert selection.rejected[0].reason == RejectionReason.ALREADY_COMMITTED


def test_select_events_rejects_an_event_clashing_with_a_commitment() -> None:
    commitment_start = WINDOW_START + timedelta(days=2, hours=18)
    clashing_event = build_event(
        "Mahjong Night", commitment_start + timedelta(minutes=30)
    )
    profile = build_profile(
        commitments=(
            CalendarCommitment(
                title="Dinner with Charles",
                starts_at=commitment_start,
                ends_at=commitment_start + timedelta(hours=2),
                is_all_day=False,
            ),
        )
    )
    selection = select_events_to_surface(
        events=[clashing_event],
        profile=profile,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        max_distance_miles=12.0,
        max_selected_count=3,
        declined_source_event_ids=frozenset(),
    )
    assert selection.rejected[0].reason == RejectionReason.CLASHES_WITH_COMMITMENT


def test_an_all_day_entry_does_not_block_that_evening() -> None:
    event_day = WINDOW_START + timedelta(days=2)
    evening_event = build_event("Mahjong Night", event_day.replace(hour=19))
    profile = build_profile(
        commitments=(
            CalendarCommitment(
                title="Mom's birthday",
                starts_at=event_day.replace(hour=0),
                ends_at=event_day.replace(hour=0) + timedelta(days=1),
                is_all_day=True,
            ),
        )
    )
    selection = select_events_to_surface(
        events=[evening_event],
        profile=profile,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        max_distance_miles=12.0,
        max_selected_count=3,
        declined_source_event_ids=frozenset(),
    )
    assert len(selection.selected) == 1


def test_a_multi_week_course_entry_does_not_block_the_whole_window() -> None:
    evening_event = build_event(
        "Mahjong Night", WINDOW_START + timedelta(days=3, hours=19)
    )
    profile = build_profile(
        commitments=(
            CalendarCommitment(
                title="Conversation Bootcamp",
                starts_at=WINDOW_START - timedelta(days=12),
                ends_at=WINDOW_START + timedelta(days=72),
                is_all_day=False,
            ),
        )
    )
    selection = select_events_to_surface(
        events=[evening_event],
        profile=profile,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        max_distance_miles=12.0,
        max_selected_count=3,
        declined_source_event_ids=frozenset(),
    )
    assert len(selection.selected) == 1


def test_two_events_sharing_only_a_city_name_are_not_the_same_booking() -> None:
    event_start = WINDOW_START + timedelta(days=2)
    film_festival = build_event("San Francisco Brazilian Film Festival", event_start)
    profile = build_profile(
        commitments=(
            CalendarCommitment(
                title="San Francisco Tech & Finance Networking Event",
                starts_at=event_start,
                ends_at=event_start + timedelta(hours=2),
                is_all_day=False,
            ),
        )
    )
    selection = select_events_to_surface(
        events=[film_festival],
        profile=profile,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        max_distance_miles=12.0,
        max_selected_count=3,
        declined_source_event_ids=frozenset(),
    )
    assert not any(
        rejected.reason == RejectionReason.ALREADY_COMMITTED
        for rejected in selection.rejected
    )


def test_the_same_event_at_a_different_time_is_not_treated_as_already_booked() -> None:
    commitment_start = WINDOW_START + timedelta(days=2, hours=19)
    next_weeks_edition = build_event(
        "Mahjong After Dark", commitment_start + timedelta(days=7)
    )
    profile = build_profile(
        commitments=(
            CalendarCommitment(
                title="Mahjong After Dark",
                starts_at=commitment_start,
                ends_at=commitment_start + timedelta(hours=3),
                is_all_day=False,
            ),
        )
    )
    selection = select_events_to_surface(
        events=[next_weeks_edition],
        profile=profile,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        max_distance_miles=12.0,
        max_selected_count=3,
        declined_source_event_ids=frozenset(),
    )
    assert len(selection.selected) == 1


def test_select_events_never_offers_something_already_turned_down() -> None:
    declined_event = build_event(
        "Mahjong Night",
        WINDOW_START + timedelta(days=1),
        source_event_id="evt-declined",
    )
    selection = select_events_to_surface(
        events=[declined_event],
        profile=build_profile(),
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        max_distance_miles=12.0,
        max_selected_count=3,
        declined_source_event_ids=frozenset({"evt-declined"}),
    )
    assert selection.rejected[0].reason == RejectionReason.PREVIOUSLY_DECLINED


def test_select_events_rejects_events_outside_the_window() -> None:
    late_event = build_event("Mahjong Night", WINDOW_END + timedelta(days=5))
    selection = select_events_to_surface(
        events=[late_event],
        profile=build_profile(),
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        max_distance_miles=12.0,
        max_selected_count=3,
        declined_source_event_ids=frozenset(),
    )
    assert selection.rejected[0].reason == RejectionReason.OUTSIDE_TIME_WINDOW


def test_select_events_returns_best_fit_first_and_caps_the_count() -> None:
    weak_event = build_event(
        "Warehouse Rave", WINDOW_START + timedelta(days=1), source_event_id="evt-weak"
    )
    strong_event = build_event(
        "Mahjong Club Week 4",
        WINDOW_START + timedelta(days=2),
        source_event_id="evt-strong",
        is_recurring_series=True,
    )
    selection = select_events_to_surface(
        events=[weak_event, strong_event],
        profile=build_profile(),
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        max_distance_miles=12.0,
        max_selected_count=1,
        declined_source_event_ids=frozenset(),
    )
    assert len(selection.selected) == 1
    assert selection.selected[0].event.source_event_id == "evt-strong"


def test_deduplicate_events_collapses_the_same_event_across_platforms() -> None:
    event_start = WINDOW_START + timedelta(days=3)
    partiful_listing = build_event(
        "Mahjong After Dark",
        event_start,
        source=EventSourceName.PARTIFUL,
        source_event_id="pf-1",
        attending_count=None,
        venue_name="",
    )
    luma_listing = build_event(
        "Mahjong After Dark",
        event_start + timedelta(minutes=30),
        source=EventSourceName.LUMA,
        source_event_id="luma-1",
        description="Tiles and snacks",
        neighborhood="Mission",
        attending_count=36,
    )
    unique_events = deduplicate_events([partiful_listing, luma_listing])
    assert len(unique_events) == 1
    # The richer of the two listings is the one kept.
    assert unique_events[0].source == EventSourceName.LUMA


def test_deduplicate_events_keeps_distinct_events_apart() -> None:
    unique_events = deduplicate_events(
        [
            build_event(
                "Mahjong After Dark",
                WINDOW_START + timedelta(days=3),
                source_event_id="a",
            ),
            build_event(
                "Warehouse Rave", WINDOW_START + timedelta(days=3), source_event_id="b"
            ),
        ]
    )
    assert len(unique_events) == 2
