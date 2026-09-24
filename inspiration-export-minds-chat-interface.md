---
title: export minds chat interface
description: A REST API (plus a demo web UI) to list, message, and read a minds workspace's agents from outside.
thumbnail: inspiration-export-minds-chat-interface.svg
version: v1
format: v1
---

# export minds chat interface

This file is the manifest for the **export minds chat interface** inspiration (slug:
`export-minds-chat-interface`). It is the one document a future agent reads to understand,
present, and adapt this inspiration. If you are an agent in a mind that was
created from this inspiration, this file is your script: read all of it, then
follow "How to adapt it" below.

## What it is

A minds workspace already has a rich chat interface, but it lives behind the
authenticated desktop client and its underlying API has no auth of its own, so
there is no safe way to reach your agents from a phone, a script, or another
agent running elsewhere. This inspiration solves that: it puts a single
token-gated door in front of the workspace's own chat API and exposes three
capabilities to any outside caller holding one bearer token -- list the
workspace's agents (with a live idle / thinking / running-a-tool status on each),
send a message straight into any agent's live session (exactly as if you had
typed it in the web UI), and read any conversation back out (full history plus a
live server-sent-events stream). It ships two things the user can use: a REST +
SSE API (with a zero-dependency Python client) for machines and agents, and a
demo web console for humans. When it is running, the user opens the console tab,
pastes the token once, and sees an agent rail with live status dots, the selected
conversation, and a send box; the same page shows a public URL and a QR code so
the workspace's agents are reachable from a phone. Only the token-gated bridge is
ever exposed publicly -- never the raw loopback API underneath it.

## How it works

The snapshot includes these paths (each is a repo-root-relative path copied
from the original mind onto a clean default-workspace-template base):

- `system/apps/chat_bridge`
- `system/supervisord.conf`

`system/apps/chat_bridge` is the entire feature: a Flask app whose Python package
(`src/chat_bridge/`) holds the token-gated REST + SSE API (`api.py`), the auth
layer (`auth.py`), an `upstream.py` that relays to the workspace's own chat API,
the process entry points (`runner.py` for the server, `tunnel.py` for the public
tunnel), a zero-dependency Python client (`client.py`), and the demo web console
(`assets/index.html`). The package auto-joins the uv workspace through the base
`pyproject.toml`'s `system/apps/*` members glob, so no dependency wiring is
needed. `system/supervisord.conf` is the base template's supervisord config plus
exactly the two program blocks that run the feature.

At runtime two supervisord programs bring it up. `chat-bridge` first registers
port `8082` with the workspace UI by calling
`system/scripts/forward_port.py --url http://localhost:8082 --name chat-bridge`
(this is what makes the console reachable at the `/service/chat-bridge/` path
inside the workspace), then serves the Flask app on `8082`. Every `/api/*`
request must carry the bridge token; the handlers authorize, resolve the agent by
id or unique name, and proxy to the workspace's own `system_interface` chat API on
loopback `127.0.0.1:8000` -- so the bridge adds the token wall the raw API lacks.
The second program, `chat-bridge-tunnel`, runs an account-less Cloudflare quick
tunnel (`cloudflared tunnel --url`) aimed only at port `8082`, and writes the
public hostname Cloudflare assigns to `data/.state/chat-bridge/public_url.txt`;
the bridge reads that file to show the live public URL and QR code on the landing
page and to bake the reachable base URL into its `llms.txt` agent guide. Because
the tunnel points only at `8082`, only the token-gated bridge is ever public --
the raw loopback API on `8000` is never exposed.

## Recipe

This inspiration is version `v1` (front-matter `version:`).
It is not a fork of the workspace it came from -- it is DERIVED from it by the
recipe below: include these paths, leave these out, apply these
published-version rules. An update re-runs the recipe against the current
workspace and publishes the result as the next version, so anything excluded
here stays excluded even though it still exists in the source workspace. This
block is the durable home of that recipe -- a later update reads it back from
here.

```yaml
version: v1
include:
  - system/apps/chat_bridge
  - system/supervisord.conf
data_include: []
exclude:
  # Nothing beyond the two included paths ships. The app is self-contained and
  # carries no personal data, so there was nothing to strip from within it.
  []
modification_rules:
  []
```

## Prerequisites

Activation requirements: what the adopting agent must SET UP -- and must
INITIATE ITSELF during setup, before asking how to adapt -- for this
inspiration to run against the new user's own accounts/data. One line per
requirement, in this machine-readable form (greppable by `requires_`):

No prerequisites -- runs with no external permissions or secrets.

There are genuinely none in the `requires_permission:` / `requires_secret:`
sense. The bridge needs no external-service credentials and no latchkey grants:
it generates its own bearer token on first boot (a URL-safe 256-bit token written
to `data/.secrets/chat_bridge.env`), it reads only the workspace's own loopback
chat API on `127.0.0.1:8000`, and its public URL comes from the bundled
account-less Cloudflare quick tunnel, which needs no Cloudflare account. It does
not call an LLM directly (it proxies to the agents, which do), so there is no
`requires_llm` line either. Just start the two services -- `chat-bridge` and
`chat-bridge-tunnel` -- and it works against whatever workspace it runs in.

## How to adapt it

Instructions for the NEXT agent -- the one adapting this inspiration into a
new mind. This is the `use-inspiration` skill's template path; in short:

1. Read this entire file first, especially "Prerequisites" and "Holes"
   below -- Prerequisites are your SETUP agenda, Holes are your ADAPTATION
   agenda.
2. Present the inspiration to the user in plain, non-technical language: what
   it is, what it does, and what it needs from them (name the Prerequisites).
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
5. Work through each hole interactively, one at a time. Translate each into
   plain language, ask for a decision only when you genuinely need one, and
   resolve the obvious ones yourself.
6. When done, append a dated entry to "Adaptation history" below (never
   rewrite earlier entries) and commit.

## Holes

The bridge boots and works as-is; these are design gaps an adapter may want to
close, not things that block it from running:

- **One shared token, all-or-nothing access.** The single bridge token grants
  every holder full access to every conversation and every agent -- there are no
  per-agent or per-scope tokens. To narrow this, add a token registry in
  `auth.py` that maps distinct tokens to scoped grants (e.g. a token that can
  reach only one named agent, or read-only tokens that cannot send messages).
- **Ephemeral public URL.** The public address is an account-less Cloudflare
  quick tunnel: the hostname is random and changes on every restart, with no SLA.
  For a stable, shareable URL, swap `tunnel.py` for a named Cloudflare tunnel or
  an ngrok reserved domain -- the bridge itself is unchanged, only the runner
  differs.
- **The upstream API has no auth of its own.** The workspace's `system_interface`
  chat API on `127.0.0.1:8000` is unauthenticated; the bridge is what adds the
  token wall. This is fine as long as `8000` stays on loopback -- never expose it
  publicly. If you tunnel the whole workspace rather than just the bridge, put
  access control on the tunnel or restrict it to the `/service/chat-bridge/` path.
- **No rate limiting or audit logging.** The bridge does not throttle callers or
  record who sent what to which agent. For a shared or public deployment, add
  per-token rate limiting and an append-only audit log of message sends and
  reads.

## Publication history

This inspiration's changelog: what each published version changed. The PUBLISHER
appends one entry per version (newest last); earlier entries are never rewritten.
This is distinct from "Adaptation history" below, which is the ADOPTERS' log.

### v1 (2026-07-30) -- Repackaged the chat bridge onto the current default-workspace-template base (system/ layout) with its service wiring.

## Adaptation history

Each mind that adapts this inspiration appends one dated entry below. Earlier
entries are never rewritten.

### 2026-09-23 — adapted by this mind
- Adopted chat bridge app into system/apps/chat_bridge.
- Preserved existing workspace overview and welcome greeting.
- Appended chat-bridge and chat-bridge-tunnel background services to system/supervisord.conf.
- Updated package dependencies.
