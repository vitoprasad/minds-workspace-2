# Why this exists, and what it refuses to do

The reasoning behind the design. Read this before changing the ranking, because
several of the choices look wrong until you know what problem they solve.

---

## The problem is not discovery

Discovery is solved. Partiful, Luma, Eventbrite and Instagram all publish more
events than anyone can read, and nobody is short of things to do because they
could not find a list.

The real chain from "nothing on a Tuesday" to "in a room with people":

1. Know the thing exists
2. Filter to what is plausibly for you
3. Believe you will not be out of place
4. RSVP
5. **Actually go**
6. Not stand alone by the snack table

Every existing product competes on steps 1 and 2. The drop-off is between 4 and
5, and whether someone ever returns is decided at 6. The common failure is three
RSVPs in a month and zero attendances.

**So the unit of success is "did you go", not "did you find".** Every design
decision below follows from that one sentence.

---

## Three rules the code enforces

### Fewer, firmer, decided later

Surface three events, not thirty. Optionality destroys commitment: five maybes
on a Thursday means attending none. Holding many RSVPs is also a promise broken
to every host but one, and each broken RSVP makes the next one cheaper to break.

This is why there is no "browse all" mode and no auto-RSVP.

### Repeating beats one-off

Loneliness is not cured by attending events. It is cured by seeing the same
people repeatedly until they become familiar. A weekly walk club therefore
outranks a one-time gala even when the gala matches stated interests better.

`RECURRING_SERIES_BONUS` is deliberately larger than a single keyword match.
That looks like a bug if you assume interest match should dominate. It is not.

### An event must connect to something the user already does

`RejectionReason.NO_INTEREST_MATCH` is a hard gate, not a scoring penalty, and
it is the single most important filter in the file.

Without it the structural bonuses carry an event on their own -- and the events
that are reliably free, small and recurring are professional meetups. A version
of this tool without the gate recommended nothing but pitch nights and demo
days, which is precisely the noise it exists to remove.

---

## The calendar is the profile. Do not add a questionnaire.

Stated interests are aspirational. People say they are into hiking having hiked
twice in two years. What someone repeatedly puts on their calendar is behaviour,
and behaviour is the stronger signal by a wide margin.

Three consequences:

- **Never show an editable "interests profile".** That is the questionnaire
  again in a different shape.
- **Subscribed calendars count, and are often the best signal available.** A
  subscribed cultural-society or club calendar says more than anything a person
  would think to type.
- **Correct through yes/no, not through settings.** The decision log is what
  makes this improve over time; without it the tool re-derives from scratch every
  run and learns nothing.

---

## What running it against a real calendar broke

Every one of these was invisible until real data hit it. They are the reason the
filters look fussy.

| What happened | Why | The rule now |
|---|---|---|
| Every evening looked busy | Birthdays and hotel stays are all-day entries | An all-day entry marks a date, it does not occupy the hours of it |
| A three-month window went blank | A subscribed course published a term of classes as one entry spanning months | Nothing longer than 12 hours blocks an evening -- nobody is continuously busy that long |
| Unrelated events read as already-booked | Two events sharing a city name overlapped enough on words alone | The same booking must match on start time as well as title |
| Interests became "melatonin", "grogginess", "wind down" | A sleep tracker publishes hundreds of entries into the account | A calendar repeating a small vocabulary across many entries is a program, and is ignored |
| Top picks were all professional meetups | Structural bonuses carried events matching nothing | The interest-match gate above |

---

## Known weaknesses -- state them, do not paper over them

**Motivation is the real bottleneck and software may not move it.** No
recommendation makes anyone leave the house. Everything above is an attempt at
this and it may still fail. The honest test is attendance over several weeks; if
nobody goes anywhere they would otherwise have missed, the tool has failed
regardless of how good the suggestions read.

**Matching is shallow.** It is word overlap. It will connect "coffee" to a
coffee festival, but it cannot connect a weekly language lesson booked under a
tutor's name to a cultural event in that language -- the words never coincide.
Closing that gap means putting a language model in the matching step, which
costs money per run and should be a deliberate, priced decision rather than a
silent one.

**Inference is past-tense.** Mining a calendar recommends more of what someone
already does, which for a lonely person is how they got there. Reserve one of the
three slots for something outside their pattern.

**Cold start hits the target user hardest.** Someone newly arrived in a city has
almost no calendar to read and is exactly who this is for. The fallback is to
show ten real local events and take thumbs up or down: forty seconds, no prior
data, and grounded in real inventory rather than abstract categories.

**Coverage is narrow.** Partiful publishes city feeds for four metros. Outside
them only Luma answers, and Luma's mix is too professional to serve this purpose
alone.

---

## What was deliberately left out

- **Auto-RSVP.** Contradicts "fewer, firmer" and spends the user's reputation
  with hosts.
- **Eventbrite and Meetup.** Both block plain requests and need a browser, which
  is slower and more fragile. Worth adding only if inventory proves too thin.
- **Creating your own events.** A different user and a different product. The
  natural trigger is a failed search -- "nothing matched, want to host one?"
- **More than one city at a time.** Inventory density varies enormously and has
  only been checked in a few places.
