"""Dedicated public tunnel for the chat bridge, so it has a phone-ready URL.

Runs a Cloudflare *quick tunnel* (``cloudflared tunnel --url``) pointed at the
bridge's local port and nothing else. This exposes only the token-gated bridge
publicly -- not the workspace's raw, unauthenticated chat API -- so security
still rests entirely on the bridge token.

The tunnel's hostname is chosen by Cloudflare and changes each run (account-less
quick tunnels have no fixed subdomain), so the resolved URL is written to
``DATA_DIR/public_url.txt`` for the bridge to display live and bake into the
agent guide. For a stable hostname, swap this for a named Cloudflare tunnel or an
ngrok reserved domain -- the bridge is unchanged, only this runner differs.
"""

import logging
import os
import re
import subprocess
import sys
from pathlib import Path

DATA_DIR = Path(os.environ.get("CHAT_BRIDGE_DATA_DIR", "data/.state/chat-bridge"))
PORT = int(os.environ.get("CHAT_BRIDGE_PORT", "8082"))
PUBLIC_URL_FILE = DATA_DIR / "public_url.txt"

_URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

logger = logging.getLogger("chat-bridge-tunnel")


def _record_public_url(url: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_URL_FILE.write_text(url)
    logger.info("public URL: %s", url)


def read_public_url() -> str | None:
    """Return the last-recorded public URL, or None if the tunnel isn't up yet."""
    if not PUBLIC_URL_FILE.exists():
        return None
    url = PUBLIC_URL_FILE.read_text().strip()
    return url or None


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[chat-bridge-tunnel] %(message)s", stream=sys.stderr)

    # Clear any stale URL so nothing shows a dead hostname before the new tunnel
    # is reachable.
    if PUBLIC_URL_FILE.exists():
        PUBLIC_URL_FILE.unlink()

    process = subprocess.Popen(
        ["cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://localhost:{PORT}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    recorded = False
    for line in process.stdout:
        logger.info(line.rstrip())
        if not recorded:
            match = _URL_PATTERN.search(line)
            if match is not None:
                _record_public_url(match.group(0))
                recorded = True
    # cloudflared exited; drop the stale URL and propagate so supervisord restarts us.
    if PUBLIC_URL_FILE.exists():
        PUBLIC_URL_FILE.unlink()
    sys.exit(process.wait())


if __name__ == "__main__":
    main()
