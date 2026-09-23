---
name: slack-request-inbox
description: Pick up requests the user sent from their phone into a watched Slack conversation, do the work, and reply in the same thread. Use when a scheduled check reports waiting Slack requests, when the user asks what came in through Slack, or when they want to change which conversation is watched.
metadata:
  author: imbue
---

# Slack request inbox

The user's phone-side way in. They send an ordinary Slack message; a cheap
deterministic check notices it and wakes an agent here; the agent does the work
and replies in that message's thread. Slack is the transport only -- the work
itself is normal work in this workspace.

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

## Handling a run

Do this on every run, whether woken by the check or asked directly:

1. **Read the queue.** From `.agents/skills/slack-request-inbox/scripts`:

   ```bash
   uv run --project ../../../.. python slack_inbox.py pending
   ```

   It prints a JSON array, oldest request first. Each entry carries the request
   text, its `thread_ts`, a Slack `permalink`, and `raw_record_path` -- where the
   untouched Slack record was archived before anything acted on it. Reading the
   queue is safe to repeat: it never marks anything answered.

2. **Do the work, oldest request first.** Treat the text as the user's
   instruction exactly as if they had typed it in chat, and use whatever skills
   and tools the request calls for.

3. **Reply in the thread**, once per request:

   ```bash
   uv run --project ../../../.. python slack_inbox.py reply \
     --request-ts <request_ts> --thread-ts <thread_ts> --text "<reply>"
   ```

   Only this command records the request as answered, so never post to Slack
   with a bare `latchkey curl` -- a reply posted that way leaves the request
   pending and it will be answered again on the next run.

4. **If a request cannot be done, still reply.** Say plainly what blocked it.
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

`scripts/check_requests.sh` is the deterministic check, run every 5 minutes by
the cron entry in `data/.state/cron.d/slack-request-inbox` (installed live to
`/etc/cron.d/`). It reads the queue and wakes an agent **only** when something
is waiting, so an idle inbox costs nothing but one Slack call. Editing or
removing that entry is the on/off switch -- see the manage-scheduled-tasks
skill.

The 5 minute cadence sets the user's expectation: a reply arrives within a few
minutes, not instantly. Tell them that rather than implying it is live.

## What is stored

Everything under `data/.skills/slack-request-inbox/`:

| Path | What it holds |
|---|---|
| `config.toml` | The watched conversation id |
| `state.json` | The watermark, answered request timestamps, this inbox's own posted timestamps, and the threads still being polled |
| `raw/<ts>.json` | The untouched Slack record for each request, with its permalink back to the original |

The raw archive is written *before* any work is done, so the original request
survives a failed run and can always be read back or followed to its Slack
source.
