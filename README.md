# Asset Agents

The cross-agent layer over four pinned Claude Code sessions on this Mac. Each
agent owns its own repo and runs independently; this one only *observes* them
and publishes a single page you can read from a phone.

```
  Flip Watcher v1  ──┐
  RH (Robinhood)   ──┤
  Zillow scanner   ──┼──►  build-dashboard.py  ──►  Artifact  ──►  phone
  TradingView      ──┘         (reads state)        (claude.ai)
```

## Why this exists

Claude Code sessions on this machine are **local** — `isRemote: false`, transcripts
in `~/.claude/projects/`. They have no server-side copy, so they can never appear
in the Claude mobile app's conversation list, and a sidebar pin is desktop UI
state that does not travel. An Artifact is the only thing that actually reaches a
phone, so the dashboard is the sync mechanism.

## What it watches

| Session | Source of truth | Repo |
|---|---|---|
| Flip Watcher v1 | `~/flip-notifier/state.json`, `kalshi-state.json` | `flip_watcher_v1` |
| RH | Robinhood MCP connector | — (connector) |
| Zillow market scanner | `~/zillow-agent/state.json`, `config.json` | `zillow_agent_v1` |
| TradingView | `launchctl`, CDP :9222 | `tradesdontlie/tradingview-mcp` |

## Refresh

```bash
RH_DATA='{"total":…,"positions":[…]}' ./refresh.sh
```

Watcher panels read live from disk and refresh on their own. **Robinhood figures
must be passed in** — they come from an MCP connector a plain script cannot call,
so `refresh.sh` refuses to run without `RH_DATA` rather than quietly publishing a
dashboard whose portfolio is days old.

The scheduled task `asset-dashboard-refresh` does the whole loop each weekday
morning: pull the connector, run the generator, republish to the same Artifact
URL so the link never changes.

## Two things that will bite you

**The output is never committed.** `.gitignore` excludes `*.html`. A generated
dashboard contains account value, positions and P&L — the generator is the
artifact worth versioning, not its output.

**The page is a snapshot, not a feed.** An Artifact runs on claude.ai and cannot
reach localhost on this Mac, so it cannot poll. The build stamp is printed
prominently for that reason: it is the age of every figure on the page.

## Implementation notes

- Output is escaped to pure ASCII (`xmlcharrefreplace`) so the page never depends
  on a charset header being present.
- The regime bar's flex children are pinned `flex:none` — flex items shrink below
  an explicit width, which drew a 14/11 split as roughly 2/9.
- `config.markets` in zillow-agent is a **list** of `{name, zips}`, not a dict.

## Scheduled refresh

`asset-dashboard-refresh` — weekdays 08:45 local. Pulls the Robinhood connector,
runs the generator, republishes to the **same** Artifact URL so the bookmarked
link never changes:

```
https://claude.ai/code/artifact/b21714f5-9074-43cd-bfba-51787c79e0bc
```

Two guardrails are written into the task prompt:

- **Read-only on Robinhood.** It calls `get_portfolio`, `get_equity_positions` and
  `get_equity_quotes` and nothing else. The standing rule is confirm-first on every
  trade; this task has no authority to place one.
- **It refuses to publish without live portfolio figures.** If the connector is
  unavailable it reports the skip instead of shipping a page with stale money. A
  missed update is recoverable; a dashboard showing wrong numbers is not.

It also knows which alarming-looking states are deliberate — the muted Kalshi
channels (health check reads DEGRADED by design) and the $0 Agentic account — so
it does not "fix" them or flag them every morning.

Scheduled tasks run while the desktop app is open; if it is closed at 08:45 the
run happens at next launch.
