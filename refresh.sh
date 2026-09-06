#!/usr/bin/env bash
# Regenerate the dashboard from live local state.
#
# Robinhood figures cannot be read by a script — they come from an MCP
# connector — so they are passed in. Without RH_DATA the run is refused
# rather than silently publishing a dashboard with a stale portfolio.
set -euo pipefail
cd "$(dirname "$0")"
: "${RH_DATA:?RH_DATA not set — pull fresh figures from the Robinhood connector first}"
exec python3 build-dashboard.py
