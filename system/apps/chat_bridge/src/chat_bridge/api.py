"""Authenticated JSON/SSE API the bridge exposes.

Every ``/api/*`` route except the sign-in helpers requires the bridge token
(see ``auth``). Handlers are thin: they authorize, resolve the agent id-or-name,
and relay to the workspace chat API via ``upstream``. The routes mirror the
upstream shape so the client library and the browser UI use one vocabulary:

- ``POST /api/login``                      -- exchange the token for a cookie (UI)
- ``POST /api/logout``                     -- clear that cookie
- ``GET  /api/session``                    -- whether the caller is authed + upstream reachable
- ``GET  /api/agents``                     -- list agents
- ``POST /api/agents/<ident>/message``     -- send a message (ident = id or name)
- ``GET  /api/agents/<ident>/events``      -- read a transcript window
- ``GET  /api/agents/<ident>/stream``      -- live Server-Sent Events feed
"""

import io

import segno
from flask import Flask
from flask import Response
from flask import jsonify
from flask import request

from chat_bridge import tunnel
from chat_bridge import upstream
from chat_bridge.auth import COOKIE_NAME
from chat_bridge.auth import extract_presented_token
from chat_bridge.auth import is_authorized

# Paths reachable without a token: the sign-in endpoint, the UI shell, health,
# the public-URL/QR helpers the front door shows, and the agent guide (an agent
# must be able to read how to authenticate before it holds a token).
_PUBLIC_PATHS = frozenset({"/api/login", "/health", "/", "/api/public-url", "/api/qr.svg", "/llms.txt"})
_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 30


def register_api(app: Flask, token: str) -> None:
    """Register the authenticated API routes and the auth guard on ``app``."""

    @app.before_request
    def _require_token() -> Response | None:
        # The UI shell, health check, and sign-in are open; the sign-in handler
        # validates the token itself. Everything else needs a valid token.
        if request.path in _PUBLIC_PATHS or not request.path.startswith("/api/"):
            return None
        presented = extract_presented_token(dict(request.headers), dict(request.cookies))
        if not is_authorized(presented, token):
            return _error("missing or invalid token", 401)
        return None

    @app.post("/api/login")
    def _login() -> Response:
        body = request.get_json(silent=True) or {}
        presented = body.get("token") if isinstance(body, dict) else None
        if not is_authorized(presented, token):
            return _error("invalid token", 401)
        response = jsonify({"ok": True})
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=_COOKIE_MAX_AGE_SECONDS,
            httponly=True,
            samesite="Lax",
            path="/",
        )
        return response

    @app.post("/api/logout")
    def _logout() -> Response:
        response = jsonify({"ok": True})
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    @app.get("/api/session")
    def _session() -> Response:
        upstream_ok = True
        try:
            upstream.list_agents()
        except upstream.UpstreamError:
            upstream_ok = False
        return jsonify({"authorized": True, "upstream_ok": upstream_ok})

    @app.get("/api/agents")
    def _agents() -> Response:
        try:
            agents = upstream.list_agents()
        except upstream.UpstreamError as error:
            return _error(str(error), error.status_code)
        # Opt-in: compute each agent's real activity (thinking / running a tool /
        # idle) from its transcript tail. Off by default so a plain list stays cheap.
        if request.args.get("activity"):
            for agent in agents:
                agent["activity"] = upstream.compute_activity(str(agent.get("id", "")), str(agent.get("state", "")))
        return jsonify({"agents": agents})

    @app.post("/api/agents/<ident>/message")
    def _message(ident: str) -> Response:
        body = request.get_json(silent=True) or {}
        message = body.get("message") if isinstance(body, dict) else None
        if not isinstance(message, str) or not message.strip():
            return _error("body must be JSON with a non-empty 'message'", 400)
        try:
            agent_id = upstream.resolve_agent_id(ident)
            upstream.send_message(agent_id, message)
        except upstream.UpstreamError as error:
            return _error(str(error), error.status_code)
        return jsonify({"ok": True, "agent_id": agent_id})

    @app.get("/api/agents/<ident>/events")
    def _events(ident: str) -> Response:
        try:
            agent_id = upstream.resolve_agent_id(ident)
            window = upstream.get_events(agent_id, request.args)
        except upstream.UpstreamError as error:
            return _error(str(error), error.status_code)
        return jsonify(window)

    @app.get("/api/agents/<ident>/stream")
    def _stream(ident: str) -> Response:
        try:
            agent_id = upstream.resolve_agent_id(ident)
        except upstream.UpstreamError as error:
            return _error(str(error), error.status_code)
        return Response(
            upstream.stream_events(agent_id),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/public-url")
    def _public_url() -> Response:
        return jsonify({"url": tunnel.read_public_url()})

    @app.get("/api/qr.svg")
    def _qr() -> Response:
        url = tunnel.read_public_url()
        if not url:
            return _error("no public URL yet", 503)
        buffer = io.BytesIO()
        segno.make(url, error="m").save(buffer, kind="svg", scale=4, border=2, dark="#171717")
        return Response(buffer.getvalue(), mimetype="image/svg+xml", headers={"Cache-Control": "no-store"})

    @app.get("/llms.txt")
    def _llms_txt() -> Response:
        return Response(_build_agent_guide(_external_base()), mimetype="text/plain; charset=utf-8")


def _external_base() -> str:
    """Best-effort absolute base URL the caller reached this page at.

    Honors the ``X-Forwarded-*`` headers a fronting proxy/tunnel sets, so the
    guide shows the address the reader can actually use (the public tunnel host
    for an external agent, or the in-workspace ``/service/chat-bridge`` path).
    """
    proto = request.headers.get("X-Forwarded-Proto", request.scheme)
    host = request.headers.get("X-Forwarded-Host", request.host)
    prefix = request.headers.get("X-Forwarded-Prefix", "").rstrip("/")
    return f"{proto}://{host}{prefix}"


def _build_agent_guide(base: str) -> str:
    """Render the llms.txt-style API guide an agent can read to drive the bridge."""
    return f"""# Chat Bridge -- API guide for agents

You are talking to a "chat bridge": a token-authenticated door in front of a
minds workspace's chat interface. Through it you can list the workspace's agents,
send a message into any agent's chat (it is typed into that agent's session), and
read the conversation back (history + a live stream).

## Base URL
{base}

## Authentication
Send this header on EVERY request (ask the workspace owner for the token):

  Authorization: Bearer $CHAT_BRIDGE_TOKEN

Missing/invalid token -> 401. Do not put the token in a URL.

## Endpoints
GET  {base}/api/agents
     -> {{"agents": [{{"id","name","state"}}, ...]}}

POST {base}/api/agents/<id-or-name>/message
     body: {{"message": "..."}}   -> {{"ok": true, "agent_id": "..."}}
     The message is delivered into that agent's live session.

GET  {base}/api/agents/<id-or-name>/events?limit=50
     -> {{"events": [...], "offset": N, "total": M}}
     Query: limit, before=<event_id>, after=<event_id>, offset=<int>.

GET  {base}/api/agents/<id-or-name>/stream
     -> text/event-stream; each frame is `data: <event json>`.

An agent may be addressed by id or by its unique name.
Errors: 401 unauthenticated, 404 unknown agent, 409 ambiguous name, 502 upstream.

## Event shapes
user_message      {{type, event_id, role:"user", content, timestamp}}
assistant_message {{type, event_id, role:"assistant", text, tool_calls:[{{tool_name}}], timestamp}}
tool_result       {{type, event_id, tool_name, tool_call_id, timestamp}}

## Examples
curl -s -H "Authorization: Bearer $CHAT_BRIDGE_TOKEN" "{base}/api/agents"

curl -s -X POST -H "Authorization: Bearer $CHAT_BRIDGE_TOKEN" \\
  -H "Content-Type: application/json" -d '{{"message":"status?"}}' \\
  "{base}/api/agents/<id-or-name>/message"

curl -s -H "Authorization: Bearer $CHAT_BRIDGE_TOKEN" \\
  "{base}/api/agents/<id-or-name>/events?limit=20"

## Python client
A zero-dependency client (chat_bridge/client.py) is available; set
CHAT_BRIDGE_URL={base} and CHAT_BRIDGE_TOKEN, then:
  client = ChatBridgeClient(url, token); client.send("<name>", "hi")
"""


def _error(message: str, status_code: int) -> Response:
    """Return a uniform ``{error}`` JSON body with ``status_code``."""
    response = jsonify({"error": message})
    response.status_code = status_code
    return response
