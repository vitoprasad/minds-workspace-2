#!/usr/bin/env bash
#
# check_requests.sh -- the deterministic tick for the Slack request inbox. A
# cron entry runs it through run_job.sh (--every 5m), and it wakes the
# slack-request-inbox agent only when the watched Slack conversation actually
# has an unanswered message. With nothing waiting it exits silently, so no
# agent (and no model cost) is spent on an idle inbox.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
INBOX_ROOT="$ROOT/data/.skills/slack-request-inbox"
RUN_AUTOMATION="$ROOT/system/libs/automations/run_automation.sh"

log() { printf '%s slack_request_inbox_check: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

if [ ! -f "$INBOX_ROOT/config.toml" ]; then
    log "no Slack conversation configured yet; nothing to check"
    exit 0
fi

cd "$SCRIPT_DIR"
PENDING_COUNT="$(uv run --project "$ROOT" python slack_inbox.py --root "$INBOX_ROOT" pending --count-only)"

if [ "$PENDING_COUNT" -eq 0 ]; then
    exit 0
fi

log "$PENDING_COUNT request(s) waiting; waking the agent"
exec bash "$RUN_AUTOMATION" slack-request-inbox
