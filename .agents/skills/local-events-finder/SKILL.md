---
name: local-events-finder
description: Find local events worth actually going to, chosen from the user's own calendar rather than a questionnaire. Reads Partiful and Luma city feeds, drops what they cannot attend (sold out, too far, clashes with something already booked, already turned down once), and ranks what is left with repeating events favoured over one-offs. Use when the user asks what is on near them, wants something to do, asks whether a specific event is worth going to, or when a scheduled run surfaces the week's picks.
metadata:
  author: imbue
---

# Local events finder

Finds a small number of events the user would genuinely turn up to, and explains
why each one was picked.

The product thesis, which the code enforces: **the unit of success is "did you
go", not "did you find".** Discovery is already solved by Partiful, Luma and
Eventbrite -- nobody lacks a list. The drop-off is between saying yes and
showing up. Two rules follow, and both are implemented rather than aspirational:

- **Fewer, firmer.** Surface three events, never thirty. Optionality destroys
  commitment: five maybes on a Thursday means attending zero.
- **Repeating beats one-off.** Loneliness is cured by seeing the same people
  again, not by attending events. A weekly walk club outranks a one-time gala
  even when the gala matches stated interests better. This is the
  `RECURRING_SERIES_BONUS` in `scripts/event_ranking.py`, and it is deliberately
  larger than a single keyword hit.

## What it reads

**The user's calendar is the profile.** There is no interests questionnaire and
you should not build one -- stated interests are aspirational ("I like hiking"
from someone who hiked twice in two years), while what someone repeatedly puts
on their calendar is behaviour. Words that recur across calendar entries become
interest keywords; work and admin words (interview, call, prep, flight) are
filtered out by `NON_INTEREST_TOKENS`. Subscribed calendars count too, and are
often the strongest signal a user has -- a subscribed cultural-society calendar
says more than anything they would think to type.

**Two event sources**, both read without any login or paid plan:

| Source | Covers | Use it for |
|---|---|---|
| Partiful | `sf`, `nyc`, `la`, `dc` only | The primary source. Its feeds are explicitly social -- "Meet New People", walk clubs, craft nights, book clubs |
| Luma | Most major cities | Secondary. Heavily skewed to tech and startup events; in SF roughly 3 in 4 listings are pitch nights or demo days |

Only public listings are read. Nothing private, no login, no scraping behind a
paywall.

## Running it

```bash
cd .agents/skills/local-events-finder/scripts
uv run --project ../../../.. python find_events.py --region sf
```

Useful flags: `--window-days` (how far ahead), `--max-distance-miles` (default
12), `--count` (default 3), `--home-latitude` / `--home-longitude` (turns on the
distance filter), `--json` (full structured output including what was rejected
and why).

## What it keeps

Everything lands under `data/.skills/local-events-finder/`:

- `raw/` -- every source response verbatim, so a change in processing never
  needs a refetch, and the original record is always recoverable
- `events.jsonl` -- the normalized listings
- `profile.json` -- what the calendar revealed
- `decisions.jsonl` -- every yes/no the user gave

**The decision log is the part that makes this better over time.** An event the
user turned down is never offered again (`PREVIOUSLY_DECLINED`). Record a verdict
with `record_decision(DECISIONS_PATH, source_event_id, "no")`. Without this loop
the tool re-derives from scratch every run and never learns, which is exactly
what a plain prompt already does -- the persistence is the whole reason this is
a skill.

## Surfacing results to the user

Lead with the event, not the machinery. Give the name, when, where, the cost,
and the one reason it was picked. Always include the link back to the listing so
they can read the original themselves -- never make them ask for it.

Say plainly when a source came back empty or a region is not covered; do not
quietly return a short list as though it were the whole picture.

## Known limits -- state these honestly, do not paper over them

- **Partiful covers four cities.** Outside `sf`/`nyc`/`la`/`dc` only Luma
  responds, which for a social recommendation is thin to the point of unhelpful.
- **Partiful does not publish prices** in its city feeds, so cost is recorded as
  unstated rather than free. Never present a Partiful event as free.
- **Partiful's "SF" region spans all of Northern California**, including towns
  two hours out. The distance filter is required, not optional -- but it only
  works when a home coordinate is supplied, since without one distance is
  unknown and nothing is dropped for being far.
- **Partiful's internal build id rotates on every deploy**, so it is re-read from
  the live page on each run. If their page structure changes, the Partiful source
  fails and Luma still returns; `fetch_all_events` logs and continues rather than
  dying.
- **Inference is past-tense.** Mining a calendar recommends more of what someone
  already does, which for a lonely user is how they got there. Reserve one of the
  three slots for something deliberately outside their pattern.
- **Cold start is the real weakness.** Someone newly arrived in a city has almost
  no calendar to read, and is exactly who this is meant to help. For that case,
  show ten real local events and take thumbs up/down instead -- forty seconds, no
  prior data needed, and grounded in real inventory rather than abstract
  categories.

## Tests

```bash
cd .agents/skills/local-events-finder/scripts
uv run --project ../../../.. pytest .
```

The ranking and parsing tests are pure and run offline. Do not add tests that hit
the live sources -- their inventory changes hourly, so any assertion about what
is playing this week is flaky by construction.
