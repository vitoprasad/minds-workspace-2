---
name: slack-request-inbox
description: Pick up requests the user sent from their phone into a watched Slack conversation or their Telegram bot, do the work, and reply where the request came from. Use when a scheduled check reports waiting requests, when the user asks what came in through Slack or Telegram, or when they want to change which conversation is watched.
metadata:
  author: imbue
---

# Request inbox (Slack and Telegram)

The user's phone-side way in. They send an ordinary Slack or Telegram message; a
cheap deterministic check notices it and wakes an agent here; the agent does the
work and replies where the message came from. The messaging app is the transport
only -- the work itself is normal work in this workspace.

Both sides are independent: either can be set up without the other, and an
unconfigured side is a normal state that the check passes over silently.

Two rules shape the design, and both are enforced in code rather than left to
the agent:

- **A request is never lost.** A message is only recorded as answered after a
  reply has actually been posted. A run that crashes, is killed, or answers
  half the queue leaves the rest pending, and the next check offers them again.
- **Nothing is answered twice.** Every message this inbox posts is remembered,
  which matters here because the Slack credential is the *user's own account* --
  the inbox's replies are authored by the same user id as the requests, so
  author alone cannot tell them apart.

## The watched conversation

The Slack account connected to this workspace is a **guest** in its Slack
workspace, and Slack refuses channel creation to guests
(`user_is_ultra_restricted`). The inbox therefore watches the user's own
direct-message conversation with themselves by default, which needs no
creation and is private by construction. Any channel id works too, if the user
later gets one.

Which conversation is watched lives in
`data/.skills/slack-request-inbox/config.toml`. To point it somewhere else:

```bash
cd .agents/skills/slack-request-inbox/scripts
uv run --project ../../../.. python slack_inbox.py init --conversation-id <channel-or-dm-id>
```

`init` also resets the watermark to now, so messages already sitting in the
conversation are deliberately ignored -- it never opens by answering old chatter.

Find candidate conversations with
`latchkey curl -s 'https://slack.com/api/users.conversations?types=public_channel,private_channel,im&limit=100'`;
the self-DM is the `im` whose `user` equals the id from
`latchkey curl -s -X POST https://slack.com/api/auth.test`.

## The Telegram bot

Telegram is the second front door, and the one that behaves most like texting.
It runs on the user's own bot, whose token latchkey holds; reaching it needs the
`telegram-api` permission, which the user grants from the Minds app.

The bot accepts requests from **exactly one chat**: a bot's address is
guessable, so instructions from any other chat are dropped rather than acted on.
Claim that chat once, after the user has messaged the bot at least once:

```bash
cd .agents/skills/slack-request-inbox/scripts
uv run --project ../../../.. python telegram_inbox.py claim
```

`claim` adopts the chat of the first message waiting for the bot. With nothing
waiting it changes nothing and says so -- ask the user to message the bot, then
run it again.

Unlike Slack, Telegram **discards** updates once they are acknowledged, and an
acknowledgement is implicit in asking for the next offset. The code therefore
only ever acknowledges the leading run of settled updates: an unanswered request
is never confirmed away, which is what makes a crashed run recoverable.

## Handling a run

Do this on every run, whether woken by the check or asked directly. Check
**both** front doors -- either may have something waiting.

1. **Read the queue.** From `.agents/skills/slack-request-inbox/scripts`:

   ```bash
   uv run --project ../../../.. python slack_inbox.py pending
   uv run --project ../../../.. python telegram_inbox.py pending
   ```

   Each prints a JSON array, oldest request first, carrying the request text,
   the ids needed to reply, and `raw_record_path` -- where the untouched record
   was archived before anything acted on it (the Slack entries also carry a
   `permalink` back to the original message). Reading a queue is safe to repeat:
   it never marks anything answered. A front door that is not set up prints an
   empty list rather than failing.

2. **Answer within about two minutes, always.** The user is on a phone waiting
   on a notification, so speed beats completeness here in a way it does not in
   chat. Answer from what is already in front of you and from a couple of quick
   checks; if the request genuinely needs more, send the answer you have plus
   what you are still checking, and follow up in the same thread.

   Do **not** go digging through past conversations, logs, or history to enrich
   an answer the user did not ask to be enriched. A question with a one-line
   answer gets a one-line answer. Silence for ten minutes is a worse outcome
   than an answer that turns out to need a correction.

3. **Do the work, oldest request first.** Treat the text as the user's
   instruction exactly as if they had typed it in chat, and use whatever skills
   and tools the request calls for.

4. **Reply where the request came from**, once per request:

   ```bash
   uv run --project ../../../.. python slack_inbox.py reply \
     --request-ts <request_ts> --thread-ts <thread_ts> --text "<reply>"

   uv run --project ../../../.. python telegram_inbox.py reply \
     --update-id <update_id> --message-id <message_id> --text "<reply>"
   ```

   Only these commands record a request as answered, so never post with a bare
   `latchkey curl` -- a reply sent that way leaves the request pending and it
   will be answered again on the next run.

5. **If a request cannot be done, still reply.** Say plainly what blocked it.
   Silence is the one outcome the user cannot act on; an unanswered request also
   wakes an agent again on every check until something replies.

## Writing the replies

The user is reading this on a phone, so the ordinary user-facing language rules
(`.agents/shared/references/user-facing-language.md`) apply, only tighter:

- Lead with the outcome. One or two sentences is the target, and it should stand
  alone without the request in front of it.
- Never name the machinery -- no file paths, tool names, commit or step talk.
- Long jobs get two replies: one that says it is running and roughly how long,
  one when it is done. Do not leave a long job silent.
- Slack markdown is not full markdown. Plain prose survives best; `*bold*` uses
  single asterisks.

## How it is scheduled

`scripts/check_requests.sh` is the deterministic check, run every minute by the
cron entry in `data/.state/cron.d/slack-request-inbox` (installed live to
`/etc/cron.d/`). It reads both queues and wakes an agent **only** when something
is waiting, so an idle inbox costs nothing but one API call per configured front
door. Editing or removing that entry is the on/off switch -- see the
manage-scheduled-tasks skill.

**The poll is not the bottleneck, so do not promise a speed it cannot hit.**
Measured end to end, the poll contributes up to a minute, then
`run_automation.sh` spends roughly 90 seconds clearing the singleton agent's
chat and re-sending the command, and only then does the agent start thinking.
Two and a half minutes is the realistic floor with this design; tell the user
"a couple of minutes", never "instantly".

Getting below that means removing the wake cost, not polling harder -- a
long-lived process that holds a Telegram long-poll open
(`getUpdates?timeout=25` returns the moment a message lands) and an agent that
stays warm between requests. Slack cannot go that way on these credentials:
real-time Slack needs a Slack app with Socket Mode, and this workspace has a
user token, not an app.

## What is stored

Everything under `data/.skills/slack-request-inbox/`:

| Path | What it holds |
|---|---|
| `config.toml` | The watched Slack conversation id |
| `state.json` | The Slack watermark, answered request timestamps, this inbox's own posted timestamps, and the threads still being polled |
| `raw/<ts>.json` | The untouched Slack record for each request, with its permalink back to the original |
| `telegram.toml` | The one claimed Telegram chat id |
| `telegram_state.json` | The Telegram offset and the answered update ids |
| `telegram_raw/<update_id>.json` | The untouched Telegram record for each request |

The raw archive is written *before* any work is done, so the original request
survives a failed run and can always be read back or followed to its Slack
source.
