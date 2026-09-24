"""Chat bridge service: a token-authenticated door in front of the workspace chat API.

The workspace's own chat API (``system_interface`` on loopback) can list agents,
type messages into any agent's chat, and stream the conversation back -- but it
has no authentication and is only meant to be reached from inside the container
or through the desktop client. This service wraps those same capabilities behind
a single shared token so the user and their *external* agents can connect
securely and use one API for both machines and humans:

- a JSON/SSE API (``api``) external agents call with an ``Authorization: Bearer``
  header, and
- a small web page (``assets/index.html``) a human signs into once with the same
  token and then chats/reads in the browser.

Both speak to the workspace chat API via ``upstream``. See ``README.md`` for the
client library and curl recipes.

Services run from the repo root. Persistent state (here, only the
token) lives under ``data/.secrets/`` via ``auth``; ``DATA_DIR`` is reserved
for any future per-service state. The listen port defaults to this service's
assigned port but honors ``CHAT_BRIDGE_PORT`` so an editing agent can boot a
throwaway instance on a spare port (see the update-service skill).
"""

import os
from pathlib import Path

from flask import Flask
from flask import Response
from flask import send_from_directory
from werkzeug.serving import run_simple

from chat_bridge.api import register_api
from chat_bridge.auth import load_or_create_token

DATA_DIR = Path(os.environ.get("CHAT_BRIDGE_DATA_DIR", "data/.state/chat-bridge"))
PORT = int(os.environ.get("CHAT_BRIDGE_PORT", "8082"))

_ASSETS_DIR = Path(__file__).parent / "assets"


def create_app() -> Flask:
    """Build the Flask app: the UI shell plus the authenticated API."""
    app = Flask("chat_bridge", static_folder=None)
    token = load_or_create_token()

    @app.get("/")
    def index() -> Response:
        return send_from_directory(_ASSETS_DIR, "index.html")

    @app.get("/health")
    def health() -> Response:
        return Response('{"status": "ok"}', mimetype="application/json")

    register_api(app, token)
    return app


def main() -> None:
    run_simple("127.0.0.1", PORT, create_app(), threaded=True, use_reloader=False, use_debugger=False)


if __name__ == "__main__":
    main()
