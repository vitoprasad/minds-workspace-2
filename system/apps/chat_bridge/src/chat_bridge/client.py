"""Standalone client for the chat bridge -- for the user and their external agents.

Zero third-party dependencies (Python stdlib ``urllib`` only), so this single
file can be copied to any machine with Python 3 and used to reach a workspace's
chat bridge over the network. It is both:

- a library -- ``ChatBridgeClient(base_url, token)`` with ``list_agents``,
  ``send``, ``read``, and ``stream``; and
- a CLI -- ``chat-bridge-client list|send|read|tail`` (installed as a console
  script in this repo, or ``python client.py ...`` when copied elsewhere).

Configuration comes from flags or two environment variables:

  CHAT_BRIDGE_URL     base URL of the bridge, e.g.
                      https://<your-workspace-host>/service/chat-bridge
                      (or http://127.0.0.1:8000/service/chat-bridge on the host)
  CHAT_BRIDGE_TOKEN   the shared bridge token

Every call sends ``Authorization: Bearer <token>``; nothing works without it.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from collections.abc import Sequence
from http.client import HTTPResponse


class ChatBridgeError(Exception):
    """A request to the chat bridge failed (bad token, upstream error, network)."""


class ChatBridgeClient:
    """Minimal client for a workspace's chat bridge."""

    def __init__(self, base_url: str, token: str) -> None:
        if not base_url:
            raise ChatBridgeError("no bridge URL (set CHAT_BRIDGE_URL or pass --url)")
        if not token:
            raise ChatBridgeError("no bridge token (set CHAT_BRIDGE_TOKEN or pass --token)")
        self._base = base_url.rstrip("/")
        self._token = token

    def _request(self, method: str, path: str, body: dict[str, object] | None = None) -> HTTPResponse:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"{self._base}{path}", data=data, method=method)
        request.add_header("Authorization", f"Bearer {self._token}")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            return urllib.request.urlopen(request)  # noqa: S310 - operator-supplied URL
        except urllib.error.HTTPError as error:
            detail = _read_error_detail(error)
            raise ChatBridgeError(f"{error.code}: {detail}")
        except urllib.error.URLError as error:
            raise ChatBridgeError(f"cannot reach the bridge at {self._base}: {error.reason}")

    def list_agents(self) -> list[dict[str, str]]:
        """Return ``[{id, name, state}, ...]`` for every agent in the workspace."""
        with self._request("GET", "/api/agents") as response:
            return json.load(response).get("agents", [])

    def send(self, agent: str, message: str) -> str:
        """Send ``message`` into ``agent`` (id or name); return the resolved id."""
        with self._request("POST", f"/api/agents/{urllib.parse.quote(agent)}/message", {"message": message}) as response:
            return str(json.load(response).get("agent_id", agent))

    def read(self, agent: str, limit: int = 50) -> list[dict[str, object]]:
        """Return the most recent ``limit`` transcript events for ``agent``."""
        path = f"/api/agents/{urllib.parse.quote(agent)}/events?limit={int(limit)}"
        with self._request("GET", path) as response:
            return json.load(response).get("events", [])

    def stream(self, agent: str) -> Iterator[dict[str, object]]:
        """Yield transcript events as they arrive on ``agent``'s live feed."""
        with self._request("GET", f"/api/agents/{urllib.parse.quote(agent)}/stream") as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", "replace").rstrip("\n")
                if line.startswith("data: "):
                    payload = line[len("data: ") :]
                    parsed = _try_parse(payload)
                    if parsed is not None:
                        yield parsed


def _read_error_detail(error: urllib.error.HTTPError) -> str:
    body = error.read().decode("utf-8", "replace")
    parsed = _try_parse(body)
    if parsed is not None:
        error_value = parsed.get("error")
        if isinstance(error_value, str):
            return error_value
    return body or str(error.reason)


def _try_parse(text: str) -> dict[str, object] | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _render_event(event: dict[str, object]) -> str:
    kind = event.get("type")
    timestamp = str(event.get("timestamp", ""))[:19]
    if kind == "user_message":
        return f"[{timestamp}] YOU: {event.get('content') or event.get('text') or ''}"
    if kind == "assistant_message":
        text = str(event.get("text") or "").strip()
        raw_calls = event.get("tool_calls")
        calls = raw_calls if isinstance(raw_calls, list) else []
        tools = " ".join(str(c.get("tool_name", "tool")) for c in calls if isinstance(c, dict))
        suffix = f"  <tools: {tools}>" if tools else ""
        return f"[{timestamp}] AGENT: {text}{suffix}" if text else f"[{timestamp}] AGENT ran: {tools or 'tools'}"
    if kind == "tool_result":
        return f"[{timestamp}]   result <- {event.get('tool_name', 'tool')}"
    return f"[{timestamp}] {kind}"


def _client_from_args(args: argparse.Namespace) -> ChatBridgeClient:
    base_url = args.url or os.environ.get("CHAT_BRIDGE_URL", "")
    token = args.token or os.environ.get("CHAT_BRIDGE_TOKEN", "")
    return ChatBridgeClient(base_url, token)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = argparse.ArgumentParser(prog="chat-bridge-client", description="Talk to a workspace's chat bridge.")
    parser.add_argument("--url", default=None, help="bridge base URL (default: $CHAT_BRIDGE_URL)")
    parser.add_argument("--token", default=None, help="bridge token (default: $CHAT_BRIDGE_TOKEN)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list agents")

    send_parser = sub.add_parser("send", help="send a message to an agent")
    send_parser.add_argument("agent", help="agent id or name")
    send_parser.add_argument("message", nargs="+", help="message text")

    read_parser = sub.add_parser("read", help="print recent conversation for an agent")
    read_parser.add_argument("agent", help="agent id or name")
    read_parser.add_argument("--limit", type=int, default=50, help="number of recent events (default 50)")
    read_parser.add_argument("--json", action="store_true", help="print raw event JSON, one per line")

    tail_parser = sub.add_parser("tail", help="follow an agent's conversation live")
    tail_parser.add_argument("agent", help="agent id or name")

    args = parser.parse_args(argv)
    try:
        client = _client_from_args(args)
        if args.command == "list":
            for agent in client.list_agents():
                print(f"{agent.get('state', ''):<10} {agent.get('name', ''):<32} {agent.get('id', '')}")
        elif args.command == "send":
            agent_id = client.send(args.agent, " ".join(args.message))
            print(f"sent to {agent_id}")
        elif args.command == "read":
            for event in client.read(args.agent, args.limit):
                print(json.dumps(event) if args.json else _render_event(event))
        elif args.command == "tail":
            for event in client.stream(args.agent):
                print(_render_event(event), flush=True)
    except ChatBridgeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
