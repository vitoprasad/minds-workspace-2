# Export Minds Chat Interface

**A REST API for driving a minds workspace's agents from outside — with a web UI
that runs on the exact same API.**

A minds workspace's agents normally take instructions only through the built-in
chat UI. This exports that capability as a small, token-authenticated **REST
API**, so your *other* agents and scripts can:

- **list** the agents in the workspace,
- **send a message into any agent's chat** (delivered straight into its live
  session), and
- **read any conversation back** — history plus a live stream.

## The point is the agentic interface

The headline is the API. It's meant to be driven by agents: clean REST + SSE
endpoints, an **`llms.txt` guide** you hand to an agent so it can drive
everything with no other instructions, and a **zero-dependency Python client**.

The included **web console and landing page are a demonstration** — a
human-friendly client that talks to the *exact same REST API* your agents use
(the same `/api/agents`, `/api/agents/<id>/message`, and `/api/agents/<id>/events`
endpoints; it just signs in with a cookie instead of a bearer header). The UI
isn't a separate system — it's proof the one interface works for people and
machines alike. It's also reachable from a phone via the workspace's share link.

## What it looks like

![The Chat Bridge web console](libs/chat_bridge/docs/webui-console.png)

*The web console: the agent list with live status (idle / thinking / running a
tool), the selected conversation, and a box to send a message — all driven by the
same REST API described below.*

## The agent guide (`llms.txt`)

Hand an agent the token and point it at `/llms.txt`; it can drive the whole API
from there, with the live base URL already filled in. A trimmed sample:

```text
# Chat Bridge -- API guide for agents

## Base URL
https://<your-bridge-host>

## Authentication
Send this header on EVERY request (ask the workspace owner for the token):
  Authorization: Bearer $CHAT_BRIDGE_TOKEN

## Endpoints
GET  /api/agents                              -> {"agents":[{id,name,state}, ...]}
POST /api/agents/<id-or-name>/message         body: {"message":"..."}
GET  /api/agents/<id-or-name>/events?limit=50 -> {events, offset, total}
GET  /api/agents/<id-or-name>/stream          -> live Server-Sent Events (SSE):
                                                 text/event-stream, one `data: <event json>` frame per new event
```

Read a conversation live over SSE:

```bash
curl -sN -H "Authorization: Bearer $CHAT_BRIDGE_TOKEN" \
  "$CHAT_BRIDGE_URL/api/agents/<agent-id-or-name>/stream"
```

## Auth & security

A single bearer token gates every agent operation (constant-time check;
generated on first boot). Without it you can see the API's shape but read no data
and take no action. The token grants full read of every conversation, so treat
it like a password. A dedicated tunnel exposes only the token-gated bridge, never
the workspace's raw internal API.

## Quickstart (agents)

```bash
export CHAT_BRIDGE_URL="https://<your-bridge-host>"
export CHAT_BRIDGE_TOKEN="<the bridge token>"

curl -s -H "Authorization: Bearer $CHAT_BRIDGE_TOKEN" "$CHAT_BRIDGE_URL/api/agents"

curl -s -X POST -H "Authorization: Bearer $CHAT_BRIDGE_TOKEN" \
  -H "Content-Type: application/json" -d '{"message":"status?"}' \
  "$CHAT_BRIDGE_URL/api/agents/<agent-id-or-name>/message"
```

Or just tell your agent: *fetch `$CHAT_BRIDGE_URL/llms.txt` and use this token.*

---

*Built as a minds inspiration — a bootable snapshot another mind can adopt and adapt.*
