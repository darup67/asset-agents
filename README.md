# Asset Agents Dashboard

The cross-agent layer over four pinned agent sessions on this Mac. Each
agent owns its own repo and runs independently; this one only *observes* them
and publishes a single page you can read from a phone.

```
  Flip Watcher v1  ──┐
  RH (Robinhood)   ──┤
  Zillow scanner   ──┼──►  build-dashboard.py  ──►  Artifact  ──►  phone
  TradingView      ──┘         (reads state)        (the artifact host)
```

## Why this exists

Agent sessions on this machine are **local** — `isRemote: false`, transcripts
in the agent's local project folder. They have no server-side copy, so they can never appear
in the mobile app's conversation list, and a sidebar pin is desktop UI
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

**The page is a snapshot, not a feed.** An Artifact runs on the artifact host and cannot
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

## BTC volatility on the dashboard

The Bitcoin 15-minute volatility index appears inside the **Flip Watcher v1**
card, because that is the session the Kalshi watcher runs from — and it is now
the only thing Kalshi alerts on.

It renders as the index value, a band chip, and the four 15-minute windows the
index averaged, drawn as bars on a **fixed 0–100¢ scale** with the LOW (28¢) and
HIGH (52¢) cuts as reference lines.

The fixed scale is deliberate. Auto-scaling would make a calm hour and a violent
one look identical, which is the one thing this block exists to distinguish. The
reference lines are positioned in pixels off the bar track rather than as a
percentage of the container — the bars occupy 48px inside a 64px box, so a
percentage pointed at the wrong height and the lines did not sit on the scale
they label.

Showing the components matters as much as the average: `47c 7c 84c 7c` reads
"one violent window inside a calm hour", which a lone `36c NORMAL` hides
completely.

Two labels are derived, not hardcoded, so they cannot go stale:
`kalshi: btc only` reflects `signals_paused`, and the old "Kalshi 24h" tile
(which counted `ALERTED` lines that BTC-only mode no longer emits, so it would
have sat frozen) is now the live vol reading.

## All accounts, not just the main one

The RH card originally showed only `••7521` because that is the account with
positions worth discussing. It was understating the picture by **$26,455** —
about 12% — because two funded accounts were simply absent:

| Account | Value | Holds |
|---|---|---|
| ••7521 Individual · margin · opt L3 | $188,965 | 16 equities + crypto + futures |
| **••4780 Cash · individual** | **$25,363** | JEPQ 419 sh |
| **••3557 Roth IRA · managed** | **$1,092** | equities |
| ••7346 Individual · managed | $0.07 | cash only |
| ••8775 Roth IRA · self-directed | $0.02 | empty |
| ••9814 Traditional IRA | $0.00 | empty |
| ••4526 Agentic · connector-tradable | $0.00 | empty |
| **All** | **$215,421** | |

Empty accounts are listed rather than hidden — an account that quietly went to
zero is worth seeing, which is how the Agentic balance going to $0 was noticed
in the first place.

Positions from more than one account now carry the account they sit in, and the
label says how many accounts are represented. Merging two accounts into one
unlabelled table is the kind of quiet aggregation that misleads later.

## Scheduled work card

A fifth card lists every scheduled thing in the stack — seven launchd agents and
four agent tasks, including `portfolio-weekly-review`.

The two halves are verified differently, and the card says so rather than
implying equal confidence:

- **launchd agents** are checked live against `launchctl` — loaded or not.
- **Agent task schedules live in the app, not on disk**, so they are recorded in
  `agents.json`. The card cross-checks the task directory and flags anything it
  does not recognise, so a task added later shows up instead of silently missing.

That drift check first fired on all 14 historical disabled tasks, which is
exactly the permanent warning people learn to ignore. They are now listed under
`retired`, so only a genuinely new task surfaces.

## No remembered balances

The Agentic-account note is derived from the balance passed in `RH_DATA`, not
written into the template. It previously read "~$7k" — a figure that was true
when typed, went to $0, and then sat wrong on the dashboard and in memory until
someone checked it against the broker.

Anything that can drift gets derived or dropped. Same reason the equity as-of
label reads the quote timestamps and the Kalshi chip reads `signals_paused`.
