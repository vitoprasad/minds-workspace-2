# chat-bridge

A token-authenticated door in front of this workspace's chat interface, so you
and your **external agents** can securely:

- list the agents in this workspace,
- send messages *into* any agent's chat (they are typed straight into that
  agent's session, exactly as if you'd typed them in the web UI), and
- read a conversation back out (history + a live stream).

There is one shared secret (the **bridge token**) and one API, used the same way
by machines and humans. The web page is at `/service/chat-bridge/`; the JSON/SSE
API is under `/service/chat-bridge/api/`.

## Why this exists

The workspace's own chat API (`system_interface`) already lists agents, delivers
messages, and streams transcripts -- but it has **no authentication** and is only
meant to be reached from inside the container or through the authenticated
desktop client. This service wraps those same endpoints behind a required token
so they can be reached safely from outside.

## The token

A strong random token is generated on first boot and stored on the workspace
host at:

```
data/.secrets/chat_bridge.env      # export CHAT_BRIDGE_TOKEN="..."
```

Read it there (or ask the workspace agent for it). Treat it like a password:
anyone with the token can message every agent and read every transcript. To
rotate it, delete that file and restart the service (`supervisorctl restart
chat-bridge`) -- a new token is generated.

## Where to reach it

- **On the workspace host / another agent in this workspace:**
  `http://127.0.0.1:8000/service/chat-bridge`
- **From your laptop or an external agent:** the workspace's public URL +
  `/service/chat-bridge` -- e.g. `https://<your-workspace-host>/service/chat-bridge`
  (the same host you open the workspace at). Set that as `CHAT_BRIDGE_URL`.

## Humans: the web page

Open the **chat-bridge** tab (or browse to `/service/chat-bridge/`). Paste the
token once to establish a link; the page then lists agents, shows each
conversation live, and lets you send messages. Every message row has a small
`raw` control that reveals the underlying event record.

## Agents / scripts: the API

Authenticate with `Authorization: Bearer <token>` on every request.

```bash
BASE="https://<your-workspace-host>/service/chat-bridge"
TOKEN="<the bridge token>"

# list agents
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/api/agents"

# send a message (agent id OR name)
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"message":"hello from an external agent"}' \
  "$BASE/api/agents/<agent-id-or-name>/message"

# read the last 50 events
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/api/agents/<agent-id-or-name>/events?limit=50"

# follow the live conversation (Server-Sent Events)
curl -sN -H "Authorization: Bearer $TOKEN" "$BASE/api/agents/<agent-id-or-name>/stream"
```

### Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET`  | `/api/agents` | list agents (`{agents: [{id, name, state}]}`) |
| `POST` | `/api/agents/<id-or-name>/message` | send `{message}`; returns `{ok, agent_id}` |
| `GET`  | `/api/agents/<id-or-name>/events` | transcript window `{events, offset, total}`; query: `limit`, `before`, `after`, `offset` |
| `GET`  | `/api/agents/<id-or-name>/stream` | live `text/event-stream` of new events |
| `POST` | `/api/login` | exchange `{token}` for a cookie (used by the web page) |
| `GET`  | `/api/session` | check auth + upstream reachability |

An agent can be addressed by its id or its (unique) name. Missing/invalid token
-> `401`; unknown agent -> `404`; ambiguous name -> `409`.

## Agents / scripts: the Python client

`src/chat_bridge/client.py` is a **single file with zero third-party
dependencies** -- copy it to any machine with Python 3, or use it in-repo.

```bash
export CHAT_BRIDGE_URL="https://<your-workspace-host>/service/chat-bridge"
export CHAT_BRIDGE_TOKEN="<the bridge token>"

# in this repo:
uv run chat-bridge-client list
uv run chat-bridge-client send <agent-id-or-name> "hello there"
uv run chat-bridge-client read <agent-id-or-name> --limit 20
uv run chat-bridge-client tail <agent-id-or-name>

# copied elsewhere (stdlib only):
python client.py list
```

As a library:

```python
from chat_bridge.client import ChatBridgeClient

client = ChatBridgeClient("https://<your-workspace-host>/service/chat-bridge", token)
for agent in client.list_agents():
    print(agent["name"], agent["state"])
client.send("some-agent-name", "kick off the nightly build")
for event in client.stream("some-agent-name"):   # live feed
    print(event["type"])
```

## Security notes

- The bridge enforces the token itself, so it is safe regardless of whether the
  transport in front of it (Cloudflare tunnel, etc.) adds its own auth.
- Comparison is constant-time; the token file is written `0600`.
- **Caveat:** this bridge secures *its own* endpoints. The workspace's raw
  `system_interface` API (`/api/agents/...` on port 8000) remains
  unauthenticated. That is only a concern if port 8000 is exposed publicly
  *without* the token wall -- if your workspace is reached only through the
  authenticated desktop client, the raw API is already behind that. If you
  expose the workspace over a public tunnel, put access control on the tunnel
  (e.g. Cloudflare Access) or restrict it to the `/service/chat-bridge/` path so
  the raw API is not reachable unauthenticated.

## Configuration

| Env var | Default | Meaning |
|---------|---------|---------|
| `CHAT_BRIDGE_TOKEN` | generated to `data/.secrets/chat_bridge.env` | the shared secret |
| `CHAT_BRIDGE_PORT` | `8082` | listen port |
| `CHAT_BRIDGE_UPSTREAM` | `http://127.0.0.1:8000` | the workspace chat API to front |
| `CHAT_BRIDGE_DATA_DIR` | `data/.state/chat-bridge` | reserved for future per-service state |
