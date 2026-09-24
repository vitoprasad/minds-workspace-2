"""Client for the workspace chat API the bridge sits in front of.

The bridge never re-implements chat delivery or transcript reading -- it proxies
to the ``system_interface`` HTTP API already running on loopback (default
``http://127.0.0.1:8000``), which is exactly the API the workspace web UI uses:

- ``GET  /api/agents``                    -- list agents (id, name, state)
- ``POST /api/agents/<id>/message``       -- type a message into an agent's chat
- ``GET  /api/agents/<id>/events``        -- read a window of the transcript
- ``GET  /api/agents/<id>/stream``        -- Server-Sent Events live feed

Those upstream routes key on the agent *id*. The bridge additionally lets a
caller name an agent (``resolve_agent_id``), so a human or agent can say
"minds-primary" instead of copying a uuid.
"""

import os
from collections.abc import Iterator
from collections.abc import Mapping
from datetime import datetime
from datetime import timezone

import httpx

# Where the workspace chat API listens. Overridable so a throwaway bridge can
# point at a throwaway system_interface during editing/testing.
UPSTREAM_BASE = os.environ.get("CHAT_BRIDGE_UPSTREAM", "http://127.0.0.1:8000").rstrip("/")

# Short connect/read timeout for request/response calls; the streaming call
# below uses its own (unbounded read) timeout because SSE feeds stay open.
_TIMEOUT = httpx.Timeout(10.0, read=30.0)
_STREAM_TIMEOUT = httpx.Timeout(10.0, read=None)


class UpstreamError(Exception):
    """A failure talking to the workspace chat API, carrying an HTTP status.

    ``status_code`` is what the bridge should return to its own caller (e.g. 502
    when the interface is unreachable, or the upstream's own 404/500 relayed).
    """

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def list_agents() -> list[dict[str, str]]:
    """Return the list of agents as ``[{id, name, state}, ...]``."""
    try:
        response = httpx.get(f"{UPSTREAM_BASE}/api/agents", timeout=_TIMEOUT)
    except httpx.HTTPError as error:
        raise UpstreamError(f"cannot reach the chat interface: {error}", 502)
    if response.status_code != 200:
        raise UpstreamError(f"chat interface returned status {response.status_code}", 502)
    payload = response.json()
    agents = payload.get("agents", []) if isinstance(payload, dict) else payload
    return [agent for agent in agents if isinstance(agent, dict)]


def select_agent(agents: list[dict[str, str]], identifier: str) -> str:
    """Pick the id of the agent named/identified by ``identifier`` from ``agents``.

    An exact id match wins. Otherwise an exact (then case-insensitive) name
    match is used; an ambiguous name (more than one agent) is a 409 so the
    caller disambiguates rather than messaging the wrong agent, and no match is
    a 404. This is the pure resolution logic, separated so it can be tested
    without the network.
    """
    for agent in agents:
        if agent.get("id") == identifier:
            return identifier
    by_name = [agent for agent in agents if agent.get("name") == identifier]
    if not by_name:
        lowered = identifier.lower()
        by_name = [agent for agent in agents if agent.get("name", "").lower() == lowered]
    if len(by_name) == 1:
        return by_name[0]["id"]
    if len(by_name) > 1:
        raise UpstreamError(f"'{identifier}' matches {len(by_name)} agents; use the id", 409)
    raise UpstreamError(f"no agent with id or name '{identifier}'", 404)


def resolve_agent_id(identifier: str) -> str:
    """Resolve an agent id-or-name to its id by listing agents (see ``select_agent``)."""
    return select_agent(list_agents(), identifier)


# Lifecycle states in which an agent's process is actually up; anything else is idle.
_RUNNING_STATES = frozenset({"RUNNING", "RUNNING_UNKNOWN_AGENT_TYPE"})
_ACTIVITY_TAIL_LIMIT = 60
# A running-but-active tail whose newest event is older than this is treated as idle
# (a turn abandoned by a restart) -- an approximation of the workspace's own
# process-start guard, which relies on a marker file the bridge can't see over HTTP.
_STALE_SECONDS = 900


def compute_activity(agent_id: str, state: str) -> str:
    """Return ``"thinking"``, ``"tool_running"``, or ``"idle"`` for an agent.

    Mirrors the workspace's own logic: a non-running lifecycle state is idle;
    otherwise the transcript tail decides -- an unmatched tool call means a tool
    is running, a trailing user/tool message means the agent is thinking, and a
    trailing assistant reply (or empty tail) means it is idle and awaiting input.
    """
    if state not in _RUNNING_STATES:
        return "idle"
    try:
        window = get_events(agent_id, {"limit": str(_ACTIVITY_TAIL_LIMIT)})
    except UpstreamError:
        return "idle"
    events = window.get("events")
    if not isinstance(events, list) or not events:
        return "idle"
    activity = _derive_activity(events)
    if activity != "idle" and _tail_is_stale(events[-1]):
        return "idle"
    return activity


def _derive_activity(events: list[dict[str, object]]) -> str:
    """Classify the transcript tail into thinking / tool_running / idle."""
    if not events:
        return "idle"
    pending: set[str] = set()
    for event in events:
        event_type = event.get("type")
        if event_type == "assistant_message":
            calls = event.get("tool_calls")
            if isinstance(calls, list):
                for call in calls:
                    call_id = call.get("tool_call_id") if isinstance(call, dict) else None
                    if isinstance(call_id, str):
                        pending.add(call_id)
        elif event_type == "tool_result":
            result_id = event.get("tool_call_id")
            if isinstance(result_id, str):
                pending.discard(result_id)
    if pending:
        return "tool_running"
    last_type = events[-1].get("type")
    if last_type in ("user_message", "tool_result"):
        return "thinking"
    return "idle"


def _tail_is_stale(last_event: dict[str, object]) -> bool:
    """True if the newest event is old enough to be an abandoned turn (fail-open)."""
    raw = last_event.get("timestamp")
    if not isinstance(raw, str) or not raw:
        return False
    try:
        stamped = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - stamped).total_seconds() > _STALE_SECONDS


def send_message(agent_id: str, message: str) -> None:
    """Send ``message`` into the agent's chat (relaying upstream failures)."""
    try:
        response = httpx.post(
            f"{UPSTREAM_BASE}/api/agents/{agent_id}/message",
            json={"message": message},
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as error:
        raise UpstreamError(f"cannot reach the chat interface: {error}", 502)
    if response.status_code != 200:
        raise UpstreamError(_detail_or_status(response), response.status_code)


def get_events(agent_id: str, params: Mapping[str, str]) -> dict[str, object]:
    """Return a transcript window ``{events, offset, total}`` for the agent."""
    try:
        response = httpx.get(
            f"{UPSTREAM_BASE}/api/agents/{agent_id}/events",
            params=dict(params),
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as error:
        raise UpstreamError(f"cannot reach the chat interface: {error}", 502)
    if response.status_code != 200:
        raise UpstreamError(_detail_or_status(response), response.status_code)
    return response.json()


def stream_events(agent_id: str) -> Iterator[bytes]:
    """Yield raw Server-Sent-Event bytes from the agent's live feed.

    Iterates the upstream SSE response line by line and re-emits each line, so
    the bridge is a transparent pass-through of the same ``text/event-stream``
    the workspace UI consumes. The generator ends when the upstream feed closes.
    """
    try:
        with httpx.stream(
            "GET",
            f"{UPSTREAM_BASE}/api/agents/{agent_id}/stream",
            timeout=_STREAM_TIMEOUT,
        ) as response:
            if response.status_code != 200:
                raise UpstreamError(f"chat interface returned status {response.status_code}", 502)
            for line in response.iter_lines():
                yield f"{line}\n".encode()
    except httpx.HTTPError as error:
        raise UpstreamError(f"lost connection to the chat interface: {error}", 502)


def _detail_or_status(response: httpx.Response) -> str:
    """Extract the upstream ``{detail}`` error message, or fall back to status."""
    content_type = response.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        body = response.json()
        if isinstance(body, dict) and isinstance(body.get("detail"), str):
            return body["detail"]
    return f"chat interface returned status {response.status_code}"
