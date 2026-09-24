"""Token authentication for the chat bridge.

The bridge is a door in front of the workspace's (otherwise unauthenticated)
chat API, so every request that reaches an agent must carry a shared secret.
The secret is a single strong random token that lives in
``data/.secrets/chat_bridge.env`` -- the same per-secret ``*.env`` convention
the Cloudflare tunnel token uses -- and is generated on first boot if absent.

Callers present the token one of three ways, checked in this order:

- ``Authorization: Bearer <token>`` -- the header external agents/scripts use.
- ``X-Chat-Bridge-Token: <token>`` -- an equivalent plain header.
- a ``chat_bridge_token`` cookie -- what the browser UI uses after a human
  pastes the token into the sign-in page once, since ``EventSource`` (used for
  the live stream) cannot set request headers.

Comparison is constant-time to avoid leaking the token through timing.
"""

import hmac
import os
import re
import secrets
import stat
from collections.abc import Mapping
from pathlib import Path

TOKEN_ENV_VAR = "CHAT_BRIDGE_TOKEN"
COOKIE_NAME = "chat_bridge_token"

# Per-secret env file, mirroring data/.secrets/cloudflare_tunnel.env. Each
# writer owns its own file so secrets never clobber one another.
_SECRETS_DIR = Path("data/.secrets")
_TOKEN_FILE = _SECRETS_DIR / "chat_bridge.env"
_TOKEN_PATTERN = re.compile(
    r"""^export\s+CHAT_BRIDGE_TOKEN=["']?([^"'\s]+)["']?\s*$""", re.MULTILINE
)
_BEARER_PREFIX = "Bearer "


def _read_token_file(path: Path) -> str | None:
    """Return the token recorded in ``path``, or None if the file/line is absent."""
    if not path.exists():
        return None
    match = _TOKEN_PATTERN.search(path.read_text())
    if match is None:
        return None
    return match.group(1)


def _write_token_file(path: Path, token: str) -> None:
    """Write ``token`` to ``path`` as a sourceable env line with 0600 perms."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'export {TOKEN_ENV_VAR}="{token}"\n')
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def load_or_create_token() -> str:
    """Return the bridge token, generating and persisting one on first boot.

    An explicit ``CHAT_BRIDGE_TOKEN`` in the environment wins (useful for tests
    and for a throwaway instance). Otherwise the token is read from
    ``data/.secrets/chat_bridge.env``, and if that file has no token yet a
    fresh URL-safe 256-bit token is generated and stored there.
    """
    env_token = os.environ.get(TOKEN_ENV_VAR)
    if env_token:
        return env_token
    existing = _read_token_file(_TOKEN_FILE)
    if existing is not None:
        return existing
    token = secrets.token_urlsafe(32)
    _write_token_file(_TOKEN_FILE, token)
    return token


def token_file_path() -> Path:
    """Return the path where the persisted token lives (for operator messaging)."""
    return _TOKEN_FILE


def extract_presented_token(headers: Mapping[str, str], cookies: Mapping[str, str]) -> str | None:
    """Pull the caller's token from the Authorization/X-header/cookie, in order."""
    authorization = headers.get("Authorization", "")
    if authorization.startswith(_BEARER_PREFIX):
        candidate = authorization[len(_BEARER_PREFIX) :].strip()
        if candidate:
            return candidate
    header_token = headers.get("X-Chat-Bridge-Token", "").strip()
    if header_token:
        return header_token
    cookie_token = cookies.get(COOKIE_NAME, "").strip()
    if cookie_token:
        return cookie_token
    return None


def is_authorized(presented: str | None, expected: str) -> bool:
    """Return True iff ``presented`` matches ``expected`` (constant-time)."""
    if not presented:
        return False
    return hmac.compare_digest(presented, expected)
