#!/usr/bin/env bash
#
# check_requests.sh -- the deterministic tick for the request inbox. A cron
# entry runs it through run_job.sh (--every 5m), and it wakes the
# slack-request-inbox agent only when Slack or Telegram actually has an
# unanswered message. With nothing waiting it exits silently, so no agent (and
# no model cost) is spent on an idle inbox. Either side may be unconfigured;
# that is a normal state, not an error.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
INBOX_ROOT="$ROOT/data/.skills/slack-request-inbox"
RUN_AUTOMATION="$ROOT/system/libs/automations/run_automation.sh"

log() { printf '%s request_inbox_check: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

cd "$SCRIPT_DIR"

SLACK_PENDING_COUNT=0
if [ -f "$INBOX_ROOT/config.toml" ]; then
    SLACK_PENDING_COUNT="$(uv run --project "$ROOT" python slack_inbox.py --root "$INBOX_ROOT" pending --count-only)"
fi

# Telegram's listener service normally ingests within seconds of a message arriving; this ingest
# is the backstop for the window where that service is down.
TELEGRAM_PENDING_COUNT=0
if [ -f "$INBOX_ROOT/telegram.toml" ]; then
    uv run --project "$ROOT" python telegram_inbox.py --root "$INBOX_ROOT" ingest > /dev/null
    TELEGRAM_PENDING_COUNT="$(uv run --project "$ROOT" python telegram_inbox.py --root "$INBOX_ROOT" pending --count-only)"
fi

TOTAL_PENDING_COUNT=$((SLACK_PENDING_COUNT + TELEGRAM_PENDING_COUNT))
if [ "$TOTAL_PENDING_COUNT" -eq 0 ]; then
    exit 0
fi

log "$SLACK_PENDING_COUNT waiting on Slack, $TELEGRAM_PENDING_COUNT on Telegram; waking the agent"

# run_automation.sh resolves the repo's own scripts relative to the working directory, so it has
# to be started from the repo root rather than from this skill's scripts directory.
cd "$ROOT"
exec bash "$RUN_AUTOMATION" slack-request-inbox
