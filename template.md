---
title: "Local Events Finder"
description: "Finds a few local events you would actually turn up to, chosen from your own calendar rather than a questionnaire."
thumbnail: "template.svg"
version: v1
format: v2
---

# Local Events Finder

This file is the manifest for the **Local Events Finder** template (slug:
`local-events-finder`). It is the one document a future agent reads to understand,
present, and adapt this template. If you are an agent in a mind that was
created from this template, this file is your script: read all of it, then
follow "How to adapt it" below.

## What it is

Finds a few local events you would actually turn up to, chosen from your own calendar rather than a questionnaire.

Most events tools help you *find* things, and finding was never the problem --
Partiful, Luma and Eventbrite publish more than anyone can read. The gap is
between saying yes and turning up: people RSVP to three things a month and go to
none. This skill is built around whether you actually go.

It reads your own calendar -- every calendar you have, including ones you merely
subscribe to -- and works out where you live, when you are genuinely free, and
what you repeatedly do. It then reads public event listings from Partiful and
Luma, throws out anything you could not or should not be offered, and hands you
**three** events with a plain-English reason for each. You ask it what is on, or
a scheduled run brings it to you. There is no interests questionnaire anywhere,
by design.

## How it works

The snapshot includes these paths (each is a repo-root-relative path copied
from the original mind onto a clean default-workspace-template base):

- `.agents/skills/local-events-finder`

`.agents/skills/local-events-finder` is a skill, not a service: nothing runs in
the background until you schedule it, and it registers no supervisord program
and no port. Its `SKILL.md` tells the agent how and when to use it, and
`references/why-this-exists.md` carries the design argument -- read that one
before changing the ranking, because several choices look like bugs without it.

`scripts/` holds five modules, layered lowest to highest:

- `event_types.py` -- the data types, and the word lists that decide what can
  never count as an interest.
- `event_ranking.py` -- pure functions: deriving interests from calendar
  entries, distance, deduplication, the filters, and the scoring.
- `event_sources.py` -- reads Partiful and Luma, normalises both into one shape,
  and writes every raw response to disk.
- `calendar_profile.py` -- reads Google Calendar through `latchkey curl` and
  builds the user profile.
- `find_events.py` -- the command that ties it together and prints the result.

Everything it keeps lives under `data/.skills/local-events-finder/`: the raw
source responses, the normalised listings, the derived profile, and a decision
log of every yes and no. That last file is what makes it improve -- an event
turned down is never offered again.

## Recipe

This template is version `v1`. It is not a fork of the
workspace it came from -- it is DERIVED from it by a recipe: include these
paths, leave these out, apply these published-version rules. An update re-runs
the recipe against the current workspace and publishes the result as the next
version, so anything excluded stays excluded even though it still exists in the
source workspace.

The recipe is machine-read, so it lives in the sibling
[`template.toml`](template.toml) -- its `[recipe]` table -- along with
the structured requirements and the environment this template needs
installed. That file is authoritative for all of it; this one holds the prose.

## Requirements

Everything the adopting mind must deal with before this template is really
theirs. Two kinds of entry, handled at different times:

- **Activation** -- what must be SET UP before anything runs, in the
  machine-readable `requires_` forms below. The adopting agent acts on these
  ITSELF, first, before asking anything.
- **Adaptation** -- what must be DECIDED or REWIRED, in prose. Worked through
  interactively with the user, after activation.

ACTIVATION:

- requires_permission: google-calendar-api / google-calendar-read-calendar-list (user-approved; the adopting agent initiates this via a latchkey permission request during setup) -- to see every calendar the user has, including subscribed ones, which are often the strongest interest signal they have.
- requires_permission: google-calendar-api / google-calendar-read-events (user-approved; initiated the same way) -- to read the entries themselves, which is what replaces the interests questionnaire: their city, their real free time, and what they repeatedly do. Read-only; it never writes to a calendar.

No secrets. The two event sources are public and are read without any login,
API key or paid plan.

No LLM. Every model-shaped decision here -- deriving interests, matching,
ranking -- is done in plain Python, so adopting this costs nothing per run and
works identically on the keyed and keyless paths.

ADAPTATION:

- **Partiful publishes city feeds for four metros only** (`sf`, `nyc`, `la`,
  `dc`). Outside them only Luma answers, and Luma's mix is far too professional
  to serve the social use case alone -- in San Francisco roughly three in four
  of its listings are pitch nights or demo days. An adopter elsewhere should
  expect thin results and may want to add a local source.
- **The default region is `sf`.** An adopter anywhere else must pass `--region`,
  or change the default in `find_events.py`.
- **The distance filter only engages with a home coordinate.** Pass
  `--home-latitude` and `--home-longitude`; without them nothing is dropped for
  being far, which matters because Partiful's "sf" region spans all of Northern
  California, including towns two hours out.
- **Matching is word overlap, and it is shallow.** It connects "coffee" to a
  coffee festival, but it cannot connect a weekly language lesson booked under
  a tutor's name to a cultural event in that language -- the words never
  coincide. Closing that gap means putting a language model in the matching
  step, which would add a per-run cost and should be a deliberate decision.
- **Nothing runs on a schedule as published.** The skill only acts when asked.
  Making it proactive -- one message a week, unprompted -- is the change that
  turns it from a tool into something that brings you plans, and is worth doing
  via the manage-scheduled-tasks skill.

## Environment

What this template needs INSTALLED, beyond what the template already has.
Declared in `template.toml`'s `[environment]` table; an adopting mind
converges it at ITS OWN pinned apt snapshot timestamp, so package versions come
out consistent with the rest of that mind's environment rather than frozen to
whatever this publisher happened to have.

Nothing extra -- runs on the stock workspace environment.

## How to adapt it

Instructions for the NEXT agent -- the one adapting this template into a
new mind. This is the `use-template` skill's template path; in short:

1. Read this entire file first, especially "Requirements" below. It holds two
   kinds of entry and they are handled at different times: the machine-readable
   `requires_` lines are ACTIVATION (set them up before anything runs), and
   the prose bullets are ADAPTATION (decide or rewire them afterwards).
2. Present the template to the user in plain, non-technical language: what
   it is, what it does, and what it needs from them (name the activation
   requirements).
3. Ask whether they want to use the same connectors (e.g. their own Slack).
   If YES: ACTIVATE FIRST -- initiate every `requires_permission` line NOW
   via a latchkey permission request (see the `latchkey` skill; the request
   opens the approval/login flow in the minds app), wire up any
   `requires_secret` values, start the services, and get the app showing
   THE USER'S OWN DATA. Done for a data-backed app means the user can open it
   and see their own data -- NOT that a service starts or an endpoint returns
   200. Then tell them it is live and to take a look.
4. Only AFTER that (or immediately, if they chose different connectors -- the
   swap is then the first adaptation) ask: "How do you want to adapt it?"
5. Work through each requirement interactively, one at a time. Translate each
   into plain language, ask for a decision only when you genuinely need one,
   and resolve the obvious ones yourself.
6. When done, append a dated entry to "Adaptation history" below (never
   rewrite earlier entries) and commit.

## Publication history

This template's changelog: what each published version changed. The PUBLISHER
appends one entry per version (newest last); earlier entries are never rewritten.
This is distinct from "Adaptation history" below, which is the ADOPTERS' log.

### v1 (2026-09-24) -- first release: calendar-derived interests, Partiful and Luma as sources, cross-source deduplication, the attendance-oriented filters and ranking, and a decision log so a declined event is never offered twice.

## Adaptation history

Each mind that adapts this template appends one dated entry below. Earlier
entries are never rewritten.

### 2026-09-24 — adapted by this mind
Merged template local-events-finder into workspace. Verified calendar connection with Google Calendar and ran initial event recommendations for San Francisco.
