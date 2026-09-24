from datetime import timezone

from calendar_profile import (
    convert_calendar_entry,
    derive_home_locality,
    is_machine_generated_calendar,
)


def test_convert_calendar_entry_reads_a_timed_entry() -> None:
    commitment = convert_calendar_entry(
        {
            "summary": "Sunset Volleyball SZN 6",
            "start": {"dateTime": "2026-09-22T19:00:00-07:00"},
            "end": {"dateTime": "2026-09-22T21:00:00-07:00"},
        }
    )
    assert commitment is not None
    assert commitment.title == "Sunset Volleyball SZN 6"
    assert commitment.starts_at.tzinfo == timezone.utc
    assert commitment.ends_at > commitment.starts_at


def test_convert_calendar_entry_flags_an_all_day_entry_as_such() -> None:
    commitment = convert_calendar_entry(
        {"summary": "Dad surgery", "start": {"date": "2026-08-31"}}
    )
    assert commitment is not None
    assert commitment.is_all_day
    assert (commitment.ends_at - commitment.starts_at).days == 1


def test_convert_calendar_entry_does_not_flag_a_timed_entry_as_all_day() -> None:
    commitment = convert_calendar_entry(
        {
            "summary": "Dinner",
            "start": {"dateTime": "2026-09-22T19:00:00-07:00"},
            "end": {"dateTime": "2026-09-22T21:00:00-07:00"},
        }
    )
    assert commitment is not None
    assert not commitment.is_all_day


def test_convert_calendar_entry_skips_an_untitled_entry() -> None:
    assert (
        convert_calendar_entry({"start": {"dateTime": "2026-09-22T19:00:00-07:00"}})
        is None
    )


def test_derive_home_locality_picks_the_most_frequent_town() -> None:
    home_locality = derive_home_locality(
        [
            {"location": "Riverside Tennis Courts, San Francisco, CA 94112, USA"},
            {
                "location": "The Corner Bistro, 100 Example Street, San Francisco, CA 94115, USA"
            },
            {"location": "Row 34, 383 Congress Street, Boston, MA 02210, USA"},
        ]
    )
    assert home_locality == "San Francisco"


def test_derive_home_locality_ignores_meeting_links() -> None:
    assert (
        derive_home_locality(
            [
                {"location": "https://us06web.zoom.us/j/84228910029"},
                {"location": "Microsoft Teams Meeting"},
                {"location": ""},
            ]
        )
        is None
    )


def test_is_machine_generated_calendar_spots_a_tracker_apps_repeated_vocabulary() -> (
    None
):
    tracker_entries = [
        {"summary": title}
        for _ in range(60)
        for title in (
            "Melatonin window",
            "Afternoon dip",
            "Morning grogginess",
            "Wind down",
        )
    ]
    assert is_machine_generated_calendar(tracker_entries)


def test_is_machine_generated_calendar_keeps_a_busy_persons_varied_calendar() -> None:
    busy_entries = [{"summary": f"Dinner with guest {index}"} for index in range(250)]
    assert not is_machine_generated_calendar(busy_entries)


def test_is_machine_generated_calendar_does_not_judge_a_small_calendar() -> None:
    repetitive_but_small = [{"summary": "Standup"} for _ in range(20)]
    assert not is_machine_generated_calendar(repetitive_but_small)
