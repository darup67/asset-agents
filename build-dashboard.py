#!/usr/bin/env python3
"""
Build the Asset Agents dashboard — a point-in-time snapshot of the four pinned
Claude Code sessions, published as an Artifact so it is readable from a phone.

Local watcher state (flip, Kalshi, Zillow) is read live from disk. Robinhood
figures arrive via RH_DATA because they come from an MCP connector a script
cannot call; refresh those by re-running with a new blob.
"""
import json, os, re, datetime, html, subprocess

HOME = os.path.expanduser("~")
FN   = os.path.join(HOME, "flip-notifier")
ZA   = os.path.join(HOME, "zillow-agent")
OUT  = "/tmp/claude-501/-Users-dhruvpatel/055dec91-ccbf-4692-a13d-b5fbbfb021ef/scratchpad/asset-agents.html"

now = datetime.datetime.now().astimezone()

def jload(p, d=None):
    try:
        with open(p) as f: return json.load(f)
    except Exception: return d if d is not None else {}

def age(iso):
    if not iso: return "unknown"
    try:
        t = datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except Exception: return "unknown"
    s = (datetime.datetime.now(datetime.timezone.utc) - t).total_seconds()
    if s < 90:   return f"{int(s)}s ago"
    if s < 5400: return f"{int(s//60)}m ago"
    if s < 172800: return f"{s/3600:.1f}h ago"
    return f"{int(s//86400)}d ago"

# ---------- 1. Flip watcher ----------
# While CHART_WATCHER_PAUSED exists the chart notifier's state.json and log are
# frozen; the headless watcher (headless-flip.js) is the live source instead.
HEADLESS = os.path.exists(os.path.join(FN, "CHART_WATCHER_PAUSED"))
HEADLESS_STALE_S = 40 * 60   # runs at :01 :03 :31 :33, so the widest gap is 28m

def _secs_since(iso):
    try:
        t = datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return (datetime.datetime.now(datetime.timezone.utc) - t).total_seconds()
    except Exception: return None

def flips_24h_tsv(path):
    n = 0
    try:
        with open(path, errors="ignore") as f:
            for l in f:
                s = _secs_since(l.split("\t", 1)[0].strip())
                if s is not None and s <= 86400: n += 1
    except Exception: pass
    return n

def last_line(path):
    try:
        with open(path, errors="ignore") as f:
            lines = [l.rstrip("\n") for l in f if l.strip()]
        return lines[-1] if lines else ""
    except Exception: return ""

if HEADLESS:
    fs   = jload(os.path.join(FN, "headless-state.json"))
    flips24 = flips_24h_tsv(os.path.join(FN, "headless-alerts.tsv"))
    # Log lines are "<ISO>  no flips · 25/25 ok · mode live".
    _hl  = last_line(os.path.join(FN, "headless-flip.log"))
    _ts, _, hl_msg = _hl.partition("  ")
    hl_msg   = hl_msg.strip()
    hl_age   = _secs_since(_ts)
    hl_stale = hl_age is None or hl_age > HEADLESS_STALE_S
    _m = re.search(r"(\d+)/(\d+) ok", hl_msg)
    hl_allok = bool(_m) and _m.group(1) == _m.group(2)
else:
    fs   = jload(os.path.join(FN, "state.json"))
    # Log lines are "<ISO>  NOTIFIED: …"; count only the last 24h, not the whole log.
    flips24 = 0
    try:
        with open(os.path.join(FN, "flip-notifier.log"), errors="ignore") as f:
            for l in f:
                if "NOTIFIED" not in l: continue
                s = _secs_since(l.split(None, 1)[0] if l.strip() else "")
                if s is not None and s <= 86400: flips24 += 1
    except Exception: pass
reg  = fs.get("regimes", {})
buys  = sorted(k.split(":")[-1] for k, v in reg.items() if v == "BUY")
sells = sorted(k.split(":")[-1] for k, v in reg.items() if v != "BUY")

# ---------- 2. Kalshi ----------
ks  = jload(os.path.join(FN, "kalshi-state.json"))
kw  = jload(os.path.join(FN, "kalshi-watchlist.json"))
kal = kw.get("alerts", {})
muted = [k for k, v in kal.items() if not v]
kseries = kw.get("series", [])
btc_only = bool(kw.get("signals_paused"))
vol_live = any((kw.get("volAlerts") or {}).values()) if kw.get("volAlerts") else True

# BTC 15m volatility index. volCache is keyed by window ticker (…-26SEP071015),
# and those sort chronologically, so sorting the keys orders the windows.
vol_index = ks.get("volIndex")
vol_band  = ks.get("volBand")
vol_wins  = [ks["volCache"][k] for k in sorted(ks.get("volCache", {}))] if ks.get("volCache") else []
VOL_LOW, VOL_HIGH = 28, 52
vol_kind = {"LOW": "ok", "HIGH": "bad"}.get(vol_band or "", "")
vol_dot  = {"LOW": "\u25cf", "NORMAL": "\u25cf", "HIGH": "\u25cf"}.get(vol_band or "", "\u25cf")

# ---------- 3. Zillow ----------
zs  = jload(os.path.join(ZA, "state.json"))
zc  = jload(os.path.join(ZA, "config.json"))
# config.markets is a list of {name, zips}
zmk = zc.get("markets", [])
if isinstance(zmk, dict):
    zmk = [{"name": k, "zips": (v.get("zips", []) if isinstance(v, dict) else v)}
           for k, v in zmk.items()]
zmk = [m for m in zmk if isinstance(m, dict)]
zzips = sum(len(m.get("zips", [])) for m in zmk)

# ---------- 3b. scheduled work ----------
# launchd agents are verified live. Claude scheduled tasks cannot be — their
# cron is held by the app, not on disk — so the config carries their schedule
# and we cross-check the task directories to surface anything unrecorded.
AGENTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agents.json")
agents_cfg = jload(AGENTS_FILE, {})
TASK_DIR = os.path.join(HOME, ".claude", "scheduled-tasks")
known_tasks = ({t["id"] for t in agents_cfg.get("claude_tasks", [])}
               | set(agents_cfg.get("retired", [])))
on_disk = set()
try:
    on_disk = {d for d in os.listdir(TASK_DIR)
               if os.path.isfile(os.path.join(TASK_DIR, d, "SKILL.md"))}
except Exception:
    pass
unknown_tasks = sorted(on_disk - known_tasks)

# ---------- 4. launchd ----------
try:
    ll = subprocess.run(["/bin/launchctl", "list"], capture_output=True, text=True, timeout=10).stdout
except Exception:
    ll = ""
def agent(name):
    # Exact label match: a substring test let com.dhruv.flipnotifier match
    # com.dhruv.flipnotifier.awake and report the notifier loaded when it wasn't.
    for line in ll.splitlines():
        p = line.split()
        if len(p) >= 3 and p[2] == name:
            return {"loaded": True, "running": p[0] != "-", "exit": p[1]}
    return {"loaded": False, "running": False, "exit": "-"}

ag_flip   = agent("com.dhruv.headlessflip" if HEADLESS else "com.dhruv.flipnotifier")
ag_awake  = agent("com.dhruv.flipnotifier.awake")
ag_kalshi = agent("com.dhruv.kalshiwatcher")
ag_zillow = agent("com.dhruv.zillowagent")

def agent_rows():
    out = []
    for a in agents_cfg.get("launchd", []):
        if a["label"] == "com.dhruv.flipnotifier" and HEADLESS:
            out.append((a["what"], a["every"], "acc", "paused"))
            continue
        if a["label"] == "com.dhruv.headlessflip" and not HEADLESS:
            continue
        st = agent(a["label"])
        out.append((a["what"], a["every"], "ok" if st["loaded"] and st["exit"] == "0"
                    else ("bad" if not st["loaded"] else "warn"),
                    "loaded" if st["loaded"] else "NOT LOADED"))
    for t in agents_cfg.get("claude_tasks", []):
        present = t["id"] in on_disk
        out.append((t["what"], t["every"], "acc" if present else "bad",
                    "scheduled task" if present else "MISSING"))
    return out

# ---------- 4b. BTC 15m cushion caller ----------
KB = os.path.join(HOME, "kalshi-btc-agent")
kb_cfg  = jload(os.path.join(KB, "config.json"))
kb_gate = jload(os.path.join(KB, "gate.json"))
kb_rows = []
try:
    with open(os.path.join(KB, "data", "evals.jsonl")) as f:
        kb_rows = [json.loads(l) for l in f.readlines()[-3000:] if l.strip()]
except Exception:
    pass
_today_et = now.astimezone(datetime.timezone(datetime.timedelta(hours=-4))).date()
def _et_day(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone(datetime.timedelta(hours=-4))).date()
kb_last = next((r for r in reversed(kb_rows) if r.get("status") in ("call", "no_call")), None)
kb_calls_today = {r["ticker"] for r in kb_rows if r.get("status") == "call" and _et_day(r["t"]) == _today_et}
kb_settled = [r for r in kb_rows if r.get("status") == "settled"]
kb_won = sum(1 for r in kb_settled if r.get("won"))
# ---------- 4c. Paper lab ----------
# results/paper.json is rewritten by every 15-min step; backtest.json only by the
# weekly report (or a manual run), so it carries its own age.
PL = os.path.join(HOME, "market-lab", "paper-lab")
pl_cfg = jload(os.path.join(PL, "config.json"))
pl_paper = jload(os.path.join(PL, "results", "paper.json"))
pl_bt = jload(os.path.join(PL, "results", "backtest.json"))
def _mtime_age(path):
    try:
        return datetime.datetime.fromtimestamp(os.path.getmtime(path), datetime.timezone.utc).isoformat()
    except Exception:
        return None
pl_paper_age = _mtime_age(os.path.join(PL, "results", "paper.json"))
pl_bt_age = _mtime_age(os.path.join(PL, "results", "backtest.json"))
PL_STRATS = ("hold", "trend", "breakout", "reversion")

def _passes(rows, s):
    r, h = rows.get(s) or {}, rows.get("hold") or {}
    return bool(r.get("bars")) and r["net"] > max(0, h.get("net", 0)) and r["first_half"] > 0 and r["second_half"] > 0

# ---------- 5. Robinhood (from connector) ----------
RH = json.loads(os.environ["RH_DATA"])

# Equity prices carry their own asof date; derive the label from it instead of
# hardcoding "Friday close". On Labor Day the last print is still Friday's, and
# a dashboard that says "live" on a shut market is worse than one that says
# nothing. asof is an ISO date from the quote timestamps.
_asof = RH.get("asof")
if _asof:
    _d = datetime.datetime.fromisoformat(_asof).date()
    _today = now.date()
    _stale_days = (_today - _d).days
    EQ_ASOF = ("live" if _stale_days == 0 and now.hour < 16
               else f"{_d.strftime('%a %-d %b')} close")
    EQ_OPEN = (_stale_days == 0)
else:
    EQ_ASOF, EQ_OPEN = "last close", False
pos = []
for p in RH["positions"]:
    q, av, last = p["q"], p["avg"], p["last"]
    val, cost = q * last, q * av
    pos.append({**p, "val": val, "cost": cost, "pl": val - cost,
                "plpct": (val - cost) / cost * 100 if cost else 0,
                "day": (last - p["prev"]) / p["prev"] * 100 if p["prev"] else 0})
pos.sort(key=lambda x: -x["pl"])
tot_val  = sum(p["val"] for p in pos)
tot_cost = sum(p["cost"] for p in pos)
tot_pl   = tot_val - tot_cost

def money(v, dec=0):
    return f"${v:,.{dec}f}"
def signed(v, dec=0):
    return f"{'+' if v >= 0 else '−'}${abs(v):,.{dec}f}"
def pct(v):
    return f"{'+' if v >= 0 else '−'}{abs(v):.1f}%"
e = html.escape

# ---------- cards ----------
if not btc_only:
    kalshi_note = ""
elif vol_live:
    kalshi_note = ('<div class="note"><span>&#9679;</span><span><b>Kalshi is in BTC-only mode.</b> '
                   'FLIP/MOVE/VOLUME signals on the other 12 series are paused; the Bitcoin volatility '
                   'index is the only watcher alert still live. Set <code>signals_paused: false</code> '
                   'to bring the rest back.</span></div>')
else:
    kalshi_note = ('<div class="note"><span>&#9679;</span><span><b>Kalshi watcher alerts are all muted.</b> '
                   'Signals on all 13 series are paused and the BTC volatility-index alert is off; BTC 15m '
                   'calls come from the cushion caller below. The health check&#8217;s DEGRADED is expected.</span></div>')

def _btc_card():
    if not kb_gate:
        return ""
    mc = kb_cfg.get("min_conf_override") or kb_gate.get("min_conf")
    al = kb_cfg.get("alerts", {})
    chans = " + ".join(k for k in ("banner", "sound", "speak", "email") if al.get(k)) or "none"
    if kb_last:
        age = int((now.timestamp() - kb_last["t"]) / 60)
        if kb_last["status"] == "call":
            last_txt = (f"{kb_last['side']} {kb_last.get('hist_hit', 0):.0%} &#183; "
                        f"${abs(kb_last.get('gap', 0)):,.0f} cushion &#183; ask {kb_last.get('ask', 0) * 100:.0f}&#162;")
        else:
            last_txt = "NO CALL"
        last_kind = "ok" if age <= 3 else "bad"
        last_html = f'<span class="v">{last_txt}</span>'
        age_html = chip(f"{age}m ago", last_kind)
    else:
        last_html, age_html = '<span class="v">--</span>', chip("no evaluations yet", "bad")
    rec = f"{kb_won}/{len(kb_settled)}" if kb_settled else "--"
    return f"""<!-- 1b ─ BTC 15m cushion caller -->
  <div class="card">
    <div class="stripe {'ok' if kb_last and (now.timestamp() - kb_last['t']) < 180 else 'bad'}"></div>
    <div class="head">
      <h2>BTC 15m cushion caller</h2>
      {age_html}
      {chip('read-only', 'acc')}
      <span class="sid">~/kalshi-btc-agent</span>
    </div>
    <div class="body">
      <div class="kv">
        <div><span class="k">Latest</span>{last_html}</div>
        <div><span class="k">Calls today</span><span class="v">{len(kb_calls_today)}</span></div>
        <div><span class="k">Settled won</span><span class="v">{rec}</span></div>
        <div><span class="k">Gate</span><span class="v">{mc:.0%} conf</span></div>
        <div><span class="k">Alerts</span><span class="v" style="font-size:12px">{chans}</span></div>
      </div>
      <div class="note"><span>&#8505;</span><span>Calls a Kalshi KXBTC15M window only when the gap to the
      strike is large against the volatility left. Backtest: calls are accurate but <b>priced in</b>
      &#8212; win rate matches the ask at every confidence level, roughly zero after fees.</span></div>
    </div>
  </div>"""

# Only surface the Agentic account when it cannot fund the strategy that
# depends on it. Deriving this from the balance means the card can never
# contradict the broker the way the previous hardcoded figure did.
ACCOUNTS = RH.get("accounts", [])
all_total = sum(a.get("total", 0) for a in ACCOUNTS) if ACCOUNTS else RH.get("total", 0)

_ag = RH.get("agentic")
if _ag is None:
    agentic_note = ""
elif _ag < 100:
    agentic_note = (
        '<div class="note bad"><span>&#9679;</span><span>The <b>Agentic account '
        '(&#8226;&#8226;4526)</b> &#8212; the only one this connector can trade &#8212; holds '
        '<b>' + money(_ag, 2) + '</b> &#8212; nothing to deploy.</span></div>')
else:
    agentic_note = (
        '<div class="note"><span>&#8505;</span><span>Agentic account '
        '(&#8226;&#8226;4526) holds <b>' + money(_ag) + '</b> &#8212; the only account this '
        'connector can place orders in.</span></div>')

# Sorted by value so the accounts that matter lead. Empty ones are still listed
# rather than hidden — an account that quietly went to zero is worth seeing.
acct_rows = "".join(
    f'<tr><td class="sym">{e(a["name"])}</td>'
    f'<td class="num" style="text-align:left;font-size:11.5px">{e(a.get("kind",""))}</td>'
    f'<td class="num">{money(a.get("total",0), 2 if a.get("total",0) < 1000 else 0)}</td>'
    f'<td class="num" style="text-align:left;font-size:11.5px">{e(a.get("holds",""))}</td></tr>'
    for a in sorted(ACCOUNTS, key=lambda x: -x.get("total", 0)))

def volbars(wins):
    """Four windows as bars on a fixed 0-100c scale, with the LOW/HIGH cuts
    drawn as reference lines. A fixed scale matters: auto-scaling would make a
    calm hour and a violent one look identical."""
    if not wins: return ""
    cells = []
    for w in wins:
        pctv = max(2.0, min(100.0, w)) 
        cls = "lo" if w < VOL_LOW else ("hi" if w > VOL_HIGH else "mid")
        cells.append(
            f'<div class="vb"><div class="vbt"><i class="{cls}" style="height:{pctv:.0f}%"></i></div>'
            f'<span>{w:.0f}c</span></div>')
    return ('<div class="vbars">'
            f'<div class="vbrule" style="bottom:{16 + VOL_LOW * 0.48:.1f}px"><span>{VOL_LOW}</span></div>'
            f'<div class="vbrule" style="bottom:{16 + VOL_HIGH * 0.48:.1f}px"><span>{VOL_HIGH}</span></div>'
            + "".join(cells) + '</div>')

def chip(txt, kind):
    return f'<span class="chip {kind}">{e(txt)}</span>'

btc_card = _btc_card()

def _paper_card():
    if not pl_cfg:
        return ""
    ag = agent("com.dhruv.paperlab")
    fresh = pl_paper_age and (now.timestamp() - datetime.datetime.fromisoformat(pl_paper_age).timestamp()) < 3600
    passes = [f"{i} {s}" for i, rows in pl_bt.items() for s in PL_STRATS[1:] if _passes(rows, s)]
    first = next((r for rows in pl_paper.values() for r in rows.values() if r.get("bars")), None)
    live_days = first["days"] if first else 0
    live = [(i, s, r["net"]) for i, rows in pl_paper.items() for s, r in rows.items()
            if s in PL_STRATS[1:] and r.get("bars")]
    best = max(live, key=lambda x: x[2]) if live else None
    killed = sum(1 for rows in pl_paper.values() for s, r in rows.items() if r.get("killed"))

    def cell(i, s):
        r = (pl_paper.get(i) or {}).get(s) or {}
        if not r.get("bars"):
            return '<td class="num" style="color:var(--faint)">--</td>'
        mark = " &#10003;" if _passes(pl_bt.get(i) or {}, s) else ""
        cls = "pos" if r["net"] > 0 else ("neg" if r["net"] < 0 else "")
        kill = ' <span style="color:var(--neg);font-size:10px">killed</span>' if r.get("killed") else ""
        return f'<td class="num {cls}">{signed(r["net"])}{mark}{kill}</td>'
    rows = "".join(f'<tr><td class="sym">{e(i)}</td>' + "".join(cell(i, s) for s in PL_STRATS) + "</tr>"
                   for i in (pl_cfg.get("instruments") or {}))
    return f"""<!-- 1c ─ Paper lab -->
  <div class="card">
    <div class="stripe {'ok' if ag['loaded'] and fresh else 'bad'}"></div>
    <div class="head">
      <h2>Paper lab</h2>
      {chip(age(pl_paper_age) if pl_paper_age else 'no results yet', 'ok' if fresh else 'bad')}
      {chip('simulated only', 'acc')}
      {chip(f"rules v{pl_cfg.get('rules_version', '?')}", 'acc')}
      <span class="sid">~/market-lab/paper-lab</span>
    </div>
    <div class="body">
      <div class="kv">
        <div><span class="k">Paper since</span><span class="v">{e(pl_cfg.get('paper_start', '')[:10])}</span></div>
        <div><span class="k">Days live</span><span class="v">{live_days}</span></div>
        <div><span class="k">Best live</span><span class="v" style="font-size:12.5px">{e(f"{best[0]} {best[1]} {signed(best[2])}") if best else "--"}</span></div>
        <div><span class="k">Books killed</span><span class="v">{killed}</span></div>
        <div><span class="k">Backtest passes</span><span class="v" style="font-size:12.5px">{e(", ".join(passes)) or "none"}</span></div>
      </div>
      <div>
        <div class="lbl">Live paper P&amp;L after costs &#183; per $10k sleeve &#183; &#10003; = passed the backtest</div>
        <div class="tblwrap">
          <table>
            <thead><tr><th>Inst</th>{''.join(f"<th>{s}</th>" for s in PL_STRATS)}</tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
      </div>
      <div class="note"><span>&#8505;</span><span>Fixed rules, simulated fills at the next bar's open with
      estimated fees and slippage. <b>No broker connection.</b> A rule only counts if it beats holding in the
      backtest <i>and</i> keeps doing it here for weeks. Backtest figures {e(age(pl_bt_age)) if pl_bt_age else 'not run yet'};
      the weekly email (Sun 6 pm) has the full report.</span></div>
    </div>
  </div>"""

paper_card = _paper_card()

# ---------- Event desk: one row per scheduled email ----------
ED = os.path.join(HOME, "market-lab", "event-desk")
IV = os.path.join(HOME, "market-iv-agent")

def _sent_log():
    out = {}
    try:
        with open(os.path.join(ED, "data", "sent.jsonl")) as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    out[r["email"]] = r          # last attempt per email wins
    except Exception:
        pass
    return out

def _handoff(path):
    h = jload(os.path.join(path, "handoff", "handoff.json"))
    if not h:
        return None
    act = [t["ticker"] for t in h.get("tickets", []) if t.get("act")]
    return {"date": h.get("date"), "run": h.get("run"), "act": act}

def _desk_card():
    sent = _sent_log()
    rows = [("watchlist", "\U0001f4cb Watchlist", "daily 8:50", None),
            ("biopharma", "\U0001f9ec Bio/pharma", "wkdy 10:05", os.path.join(IV, "data"))]
    for f in sorted(os.listdir(os.path.join(IV, "profiles")) if os.path.isdir(os.path.join(IV, "profiles")) else []):
        if f.endswith(".json"):
            prof = jload(os.path.join(IV, "profiles", f))
            key = f[:-5]
            rows.append((key, f'{prof.get("emoji", "")} {re.sub(r" IV$", "", prof.get("title", key))}', "wkdy 10:10",
                         os.path.join(IV, "data", "sectors", key)))
    today = now.date().isoformat()
    trs = []
    fresh_scans = 0
    for key, label, when, ivdir in rows:
        h = _handoff(ivdir) if ivdir else None
        if ivdir:
            if h and h["date"] == today:
                fresh_scans += 1
            scan = (f'{e(h["date"][5:])} · {("ACT " + "+".join(h["act"])) if h["act"] else "no spread passes"}'
                    if h else '<span style="color:var(--faint)">no scan yet</span>')
        else:
            scan = '<span style="color:var(--faint)">n/a</span>'
        r = sent.get(key)
        if r:
            ts = datetime.datetime.fromtimestamp(r["t"]).astimezone()
            last = (f'{"&#10003;" if r["ok"] else "&#10007; failed"} {e(ts.strftime("%a %-I:%M %p"))}'
                    + (' <span class="chip warn" style="font-size:9.5px;padding:1px 5px">test</span>' if r.get("test") else ""))
        else:
            last = '<span style="color:var(--faint)">not sent yet</span>'
        trs.append(f'<tr><td class="sym" style="font-family:inherit;font-weight:600">{e(label)}</td>'
                   f'<td class="num" style="font-size:11.5px">{e(when)}</td>'
                   f'<td class="num" style="font-size:11.5px;text-align:left;padding-left:14px">{scan}</td>'
                   f'<td class="num" style="font-size:11.5px">{last}</td></tr>')
    loaded = all(agent(l)["loaded"] for l in ("com.dhruv.eventdesk.watchlist", "com.dhruv.eventdesk.bio",
                                              "com.dhruv.eventdesk.sectors", "com.dhruv.sectoriv.open"))
    n_iv = len(rows) - 1
    wd = jload(os.path.join(HOME, "market-lab", "ops", "data", "status.json"))
    wd_age = (now - datetime.datetime.fromisoformat(wd["at"]).astimezone()).total_seconds() / 60 if wd.get("at") else None
    wd_bad = [c for c in wd.get("checks", []) if c["level"] != "ok"]
    if wd_age is None:
        wd_line = '<div class="note bad"><span>&#9679;</span><span><b>Watchdog has never run.</b></span></div>'
    elif wd_age > 40:
        wd_line = (f'<div class="note bad"><span>&#9679;</span><span><b>Watchdog silent for {wd_age:.0f} min</b> '
                   f'(runs every 15). Check <code>launchctl list | grep watchdog</code>.</span></div>')
    elif wd_bad:
        wd_line = ('<div class="note"><span>&#9679;</span><span><b>Watchdog:</b> '
                   + e("; ".join(f"{c['check']}: {c['msg']}" for c in wd_bad)) + '</span></div>')
    else:
        wd_line = (f'<div style="font-size:12px;color:var(--muted)">Watchdog: all {len(wd.get("checks", []))} checks ok, '
                   f'{wd_age:.0f} min ago. It re-runs missed scans and emails, retries refused sends, and '
                   f'health-checks the flip notifier every 15 min.</div>')
    return f"""<!-- 1d ─ Event desk emails -->
  <div class="card">
    <div class="stripe {'ok' if loaded else 'bad'}"></div>
    <div class="head">
      <h2>Event desk emails</h2>
      {chip(f"{len(rows)} emails", 'acc')}
      {chip(f"{fresh_scans}/{n_iv} scans today", 'ok' if fresh_scans == n_iv else 'warn')}
      {chip('jobs loaded' if loaded else 'job NOT loaded', 'ok' if loaded else 'bad')}
      <span class="sid">~/market-lab/event-desk</span>
    </div>
    <div class="body">
      <div class="tblwrap">
        <table>
          <thead><tr><th>Email</th><th>When</th><th style="text-align:left;padding-left:14px">Today's IV scan</th><th>Last sent</th></tr></thead>
          <tbody>{''.join(trs)}</tbody>
        </table>
      </div>
      {wd_line}
      <div class="note"><span>&#8505;</span><span>One email per S&amp;P sector (S&amp;P 500 + Nasdaq-100 + Dow),
      plus health care and the TradingView watchlist. Sector and health-care emails lead with 2&#8211;5 act-on
      bull call spreads ($10k per sector), then the top 10 by event impact. Scans run 9:45 (health care) and
      9:53 (sectors) on weekdays. Jev reads headlines once a TypeSafe key exists. Read-only: orders are entered by hand.</span></div>
    </div>
  </div>"""

desk_card = _desk_card()

flip_ok   = (ag_flip["loaded"] and not hl_stale and hl_allok) if HEADLESS \
            else (ag_flip["loaded"] and fs.get("failures", 0) == 0)

if not HEADLESS:
    flip_note = ""
elif hl_stale:
    flip_note = (f'<div class="note bad"><span>&#9679;</span><span><b>Headless watcher silent'
                 f'{"" if hl_age is None else f" for {hl_age/60:.0f} min"}.</b> '
                 f'Last log: {e(hl_msg or "none")}</span></div>')
else:
    flip_note = (f'<div class="note{"" if flip_ok else " bad"}"><span>&#9679;</span><span><b>Headless watcher</b> '
                 f'&#183; {e(age(_ts))} &#183; {e(hl_msg)}. Chart notifier retired.</span></div>')
kalshi_ok = True   # Kalshi watcher retired 2026-09-28 (its card shows the last snapshot)
zill_ok   = ag_zillow["loaded"]

rows_buy  = "".join(f'<li class="tk pos">{e(s)}</li>' for s in buys)
rows_sell = "".join(f'<li class="tk neg">{e(s)}</li>' for s in sells)

kser = "".join(f'<li class="tk">{e(s["ticker"])}</li>' for s in kseries)

posrows = "".join(
    f'<tr><td class="sym">{e(p["sym"])}'
    + (f'<span style="color:var(--faint);font-weight:400;font-size:10.5px"> {e(p["acct"])}</span>'
       if p.get("acct") else "")
    + '</td>'
    f'<td class="num">{p["q"]:,.2f}</td>'
    f'<td class="num">{money(p["val"])}</td>'
    f'<td class="num {"pos" if p["pl"]>=0 else "neg"}">{signed(p["pl"])}</td>'
    f'<td class="num {"pos" if p["pl"]>=0 else "neg"}">{pct(p["plpct"])}</td></tr>'
    for p in pos)

STAMP = now.strftime("%a %-d %b %Y · %-I:%M %p %Z")

HTML = f"""<title>Asset Agents Console</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700&family=Source+Sans+3:wght@400;600&family=JetBrains+Mono:wght@400;500;700&display=swap">
<style>
:root {{
  --ink:#0f1419; --paper:#f6f7f9; --surface:#ffffff; --sunk:#eceef2;
  --line:#d9dde4; --line-soft:#e6e9ee;
  --text:#161b22; --muted:#5b6673; --faint:#8b95a3;
  --accent:#2f6f80; --accent-soft:#e2eef1;
  --pos:#1f7a4d; --pos-soft:#e2f2e9;
  --neg:#b23c33; --neg-soft:#fae8e6;
  --warn:#9a6b1a; --warn-soft:#f9eeda;
  --radius:10px;
  --shadow:0 1px 2px rgba(15,20,25,.06), 0 4px 14px rgba(15,20,25,.05);
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --paper:#0f1419; --surface:#161c24; --sunk:#1b222c;
    --line:#2a323d; --line-soft:#222a34;
    --text:#e6eaef; --muted:#9aa5b3; --faint:#6c7684;
    --accent:#63b3c7; --accent-soft:#17313a;
    --pos:#5ec98d; --pos-soft:#14301f;
    --neg:#e8827a; --neg-soft:#331917;
    --warn:#d8a851; --warn-soft:#2e2312;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 4px 16px rgba(0,0,0,.3);
  }}
}}
:root[data-theme="dark"] {{
  --paper:#0f1419; --surface:#161c24; --sunk:#1b222c;
  --line:#2a323d; --line-soft:#222a34;
  --text:#e6eaef; --muted:#9aa5b3; --faint:#6c7684;
  --accent:#63b3c7; --accent-soft:#17313a;
  --pos:#5ec98d; --pos-soft:#14301f;
  --neg:#e8827a; --neg-soft:#331917;
  --warn:#d8a851; --warn-soft:#2e2312;
  --shadow:0 1px 2px rgba(0,0,0,.4), 0 4px 16px rgba(0,0,0,.3);
}}
* {{ box-sizing:border-box; }}
body {{
  margin:0; background:var(--paper); color:var(--text);
  font-family:"Source Sans 3",-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  font-size:15px; line-height:1.5; -webkit-font-smoothing:antialiased;
}}
.wrap {{ max-width:960px; margin:0 auto; padding:22px 18px 56px; }}

/* masthead */
.mast {{ display:flex; flex-wrap:wrap; align-items:baseline; gap:10px 14px; margin-bottom:6px; }}
h1 {{ font-family:Archivo,sans-serif; font-weight:700; font-size:clamp(22px,4.6vw,30px);
     letter-spacing:-.02em; margin:0; text-wrap:balance; }}
.stamp {{ font-family:"JetBrains Mono",monospace; font-size:11.5px; color:var(--faint);
         letter-spacing:.01em; }}
.lede {{ color:var(--muted); margin:0 0 18px; max-width:62ch; font-size:14.5px; }}

/* status strip */
.strip {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(132px,1fr)); gap:8px;
         margin-bottom:22px; }}
.st {{ background:var(--surface); border:1px solid var(--line); border-radius:var(--radius);
      padding:11px 12px; display:flex; flex-direction:column; gap:3px; }}
.st .k {{ font-size:10.5px; text-transform:uppercase; letter-spacing:.09em; color:var(--faint);
         font-weight:600; }}
.st .v {{ font-family:"JetBrains Mono",monospace; font-weight:700; font-size:17px;
         font-variant-numeric:tabular-nums; letter-spacing:-.01em; }}
.st .sub {{ font-size:11.5px; color:var(--muted); }}

/* cards */
.card {{ background:var(--surface); border:1px solid var(--line); border-radius:var(--radius);
        box-shadow:var(--shadow); margin-bottom:16px; overflow:hidden; }}
.card > .stripe {{ height:3px; background:var(--accent); }}
.stripe.ok {{ background:var(--pos); }}
.stripe.warn {{ background:var(--warn); }}
.stripe.bad {{ background:var(--neg); }}
.head {{ display:flex; flex-wrap:wrap; align-items:center; gap:8px 12px;
        padding:14px 16px 12px; border-bottom:1px solid var(--line-soft); }}
.head h2 {{ font-family:Archivo,sans-serif; font-size:16.5px; font-weight:600; margin:0;
           letter-spacing:-.01em; }}
.head .sid {{ font-family:"JetBrains Mono",monospace; font-size:11px; color:var(--faint);
             margin-left:auto; }}
.body {{ padding:14px 16px 16px; display:flex; flex-direction:column; gap:14px; }}

.chip {{ display:inline-flex; align-items:center; gap:4px; font-size:11px; font-weight:600;
        padding:2.5px 8px; border-radius:999px; letter-spacing:.02em;
        background:var(--sunk); color:var(--muted); white-space:nowrap; }}
.chip.ok   {{ background:var(--pos-soft);  color:var(--pos); }}
.chip.warn {{ background:var(--warn-soft); color:var(--warn); }}
.chip.bad  {{ background:var(--neg-soft);  color:var(--neg); }}
.chip.acc  {{ background:var(--accent-soft); color:var(--accent); }}

.kv {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(122px,1fr)); gap:10px; }}
.kv > div {{ display:flex; flex-direction:column; gap:1px; }}
.kv .k {{ font-size:10.5px; text-transform:uppercase; letter-spacing:.08em; color:var(--faint);
         font-weight:600; }}
.kv .v {{ font-family:"JetBrains Mono",monospace; font-weight:500; font-size:14.5px;
         font-variant-numeric:tabular-nums; }}

.split {{ display:flex; height:7px; border-radius:4px; overflow:hidden; background:var(--sunk); }}
.split i {{ display:block; flex:none; }}
.split .b {{ background:var(--pos); }}
.split .s {{ background:var(--neg); }}

ul.tks {{ list-style:none; margin:0; padding:0; display:flex; flex-wrap:wrap; gap:4px; }}
.tk {{ font-family:"JetBrains Mono",monospace; font-size:11.5px; padding:2px 6px;
      border-radius:4px; background:var(--sunk); color:var(--muted); }}
.tk.pos {{ background:var(--pos-soft); color:var(--pos); }}
.tk.neg {{ background:var(--neg-soft); color:var(--neg); }}

.agrid {{ display:grid; grid-template-columns:1fr auto auto; gap:6px 12px; align-items:center; }}
.agrid .n {{ font-size:13.5px; }}
.agrid .w {{ font-family:"JetBrains Mono",monospace; font-size:11.5px; color:var(--muted);
            font-variant-numeric:tabular-nums; text-align:right; }}
.volrow {{ display:flex; align-items:center; gap:14px; flex-wrap:wrap; }}
.volnum {{ font-family:"JetBrains Mono",monospace; font-weight:700; font-size:26px;
          font-variant-numeric:tabular-nums; letter-spacing:-.02em; line-height:1; }}
.volnum small {{ font-size:14px; font-weight:500; color:var(--muted); }}
.vbars {{ position:relative; display:flex; align-items:flex-end; gap:7px; height:64px;
         padding:0 26px 0 4px; }}
.vb {{ display:flex; flex-direction:column; align-items:center; gap:3px; width:30px; }}
.vbt {{ width:100%; height:48px; display:flex; align-items:flex-end;
       background:var(--sunk); border-radius:3px; overflow:hidden; }}
.vbt i {{ display:block; width:100%; border-radius:3px 3px 0 0; }}
.vbt i.lo {{ background:var(--pos); }}
.vbt i.mid {{ background:var(--accent); }}
.vbt i.hi {{ background:var(--neg); }}
.vb span {{ font-family:"JetBrains Mono",monospace; font-size:10px; color:var(--faint);
           font-variant-numeric:tabular-nums; }}
.vbrule {{ position:absolute; left:4px; right:26px; height:1px; background:var(--line);
          pointer-events:none; }}
.vbrule span {{ position:absolute; right:-24px; top:-7px; font-family:"JetBrains Mono",monospace;
               font-size:9px; color:var(--faint); }}
.lbl {{ font-size:10.5px; text-transform:uppercase; letter-spacing:.08em; color:var(--faint);
       font-weight:600; margin-bottom:5px; }}

.note {{ display:flex; gap:9px; padding:10px 12px; border-radius:8px; font-size:13.5px;
        background:var(--warn-soft); color:var(--text); border-left:3px solid var(--warn); }}
.note.bad {{ background:var(--neg-soft); border-left-color:var(--neg); }}
.note b {{ font-weight:600; }}

.tblwrap {{ overflow-x:auto; margin:0 -2px; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th {{ text-align:right; font-size:10px; text-transform:uppercase; letter-spacing:.08em;
     color:var(--faint); font-weight:600; padding:0 0 6px; border-bottom:1px solid var(--line-soft); }}
th:first-child {{ text-align:left; }}
td {{ padding:5px 0; border-bottom:1px solid var(--line-soft); }}
td.sym {{ font-family:"JetBrains Mono",monospace; font-weight:700; font-size:12.5px; }}
td.num {{ text-align:right; font-family:"JetBrains Mono",monospace;
         font-variant-numeric:tabular-nums; padding-left:14px; }}
tr:last-child td {{ border-bottom:0; }}
tfoot td {{ border-top:2px solid var(--line); border-bottom:0; padding-top:8px; font-weight:700; }}
.pos {{ color:var(--pos); }} .neg {{ color:var(--neg); }}

footer {{ margin-top:26px; padding-top:14px; border-top:1px solid var(--line);
         color:var(--faint); font-size:12.5px; }}
footer code {{ font-family:"JetBrains Mono",monospace; font-size:11.5px;
              background:var(--sunk); padding:1px 5px; border-radius:4px; color:var(--muted); }}
@media (prefers-reduced-motion:reduce) {{ * {{ animation:none!important; transition:none!important; }} }}
</style>

<div class="wrap">

  <div class="mast">
    <h1>Asset Agents</h1>
    <span class="stamp">SNAPSHOT · {e(STAMP)}</span>
  </div>
  <p class="lede">The four pinned Claude Code sessions on this Mac. Figures are read
  from local state at build time — this page does not poll, so treat the stamp above
  as the age of everything below.</p>

  <div class="strip">
    <div class="st"><span class="k">Flip Watcher</span>
      <span class="v">{len(reg)}</span>
      <span class="sub">{len(buys)} buy · {len(sells)} sell</span></div>
    <div class="st"><span class="k">Kalshi</span>
      <span class="v">{len(ks.get("markets",{}))}</span>
      <span class="sub">{len(kseries)} series tracked</span></div>
    <div class="st"><span class="k">Zillow</span>
      <span class="v">{len(zs.get("listings",{})):,}</span>
      <span class="sub">{zzips} zips · {len(zmk)} markets</span></div>
    <div class="st"><span class="k">Portfolio</span>
      <span class="v">{money(RH["total"])}</span>
      <span class="sub">main account</span></div>
  </div>

  <!-- 1 ─ Flip Watcher v1 -->
  <div class="card">
    <div class="stripe {'ok' if flip_ok and kalshi_ok else 'warn'}"></div>
    <div class="head">
      <h2>Flip Watcher v1</h2>
      {chip('healthy' if flip_ok else 'check', 'ok' if flip_ok else 'bad')}
      {chip('kalshi: btc only', 'acc') if btc_only and vol_live
        else (chip('kalshi silent', 'warn') if len(muted) == 4 else chip('kalshi alerting', 'ok'))}
      <span class="sid">this session</span>
    </div>
    <div class="body">
      <div class="kv">
        <div><span class="k">Symbols</span><span class="v">{len(reg)}</span></div>
        <div><span class="k">Last poll</span><span class="v">{e(age(fs.get("updated")))}</span></div>
        <div><span class="k">Flips 24h</span><span class="v">{flips24}</span></div>
        <div><span class="k">BTC 15m vol</span><span class="v {vol_kind}">{(f"{vol_index:.0f}c " + vol_band) if vol_index is not None else "--"}</span></div>
      </div>
      {flip_note}

      <div>
        <div class="lbl">Regime · {len(buys)} buy / {len(sells)} sell</div>
        <div class="split">
          <i class="b" style="width:{len(buys)/max(len(reg),1)*100:.1f}%"></i>
          <i class="s" style="width:{len(sells)/max(len(reg),1)*100:.1f}%"></i>
        </div>
      </div>
      <div><div class="lbl">Buy</div><ul class="tks">{rows_buy}</ul></div>
      <div><div class="lbl">Sell</div><ul class="tks">{rows_sell}</ul></div>

      <div>
        <div class="lbl">BTC 15m volatility &#183; reported every 30 min</div>
        <div class="volrow">
          <span class="volnum {vol_kind}">{f"{vol_index:.0f}" if vol_index is not None else "--"}<small>c</small></span>
          {chip(vol_band or 'no reading yet', vol_kind)}
          {volbars(vol_wins)}
        </div>
      </div>

      {kalshi_note}

      <div><div class="lbl">Kalshi series</div><ul class="tks">{kser}</ul></div>
    </div>
  </div>

  {btc_card}

  {paper_card}

  {desk_card}

  <!-- 2 ─ RH -->
  <div class="card">
    <div class="stripe {'ok' if tot_pl >= 0 else 'bad'}"></div>
    <div class="head">
      <h2>RH</h2>
      {chip('equities ' + EQ_ASOF, 'ok' if EQ_OPEN else 'warn')}
      {chip('confirm-first', 'acc')}
      <span class="sid">robinhood</span>
    </div>
    <div class="body">
      <div class="kv">
        <div><span class="k">All accounts</span><span class="v">{money(all_total)}</span></div>
        <div><span class="k">Equities</span><span class="v">{money(RH["equity"])}</span></div>
        <div><span class="k">Crypto</span><span class="v">{money(RH["crypto"])}</span></div>
        <div><span class="k">Cash</span><span class="v">{money(RH["cash"])}</span></div>
        <div><span class="k">Open P&amp;L</span>
          <span class="v {'pos' if tot_pl>=0 else 'neg'}">{signed(tot_pl)}</span></div>
        <div><span class="k">Futures</span>
          <span class="v {'pos' if RH['futures']>=0 else 'neg'}">{signed(RH['futures'])}</span></div>
      </div>

      <div>
        <div class="lbl">Accounts &#183; {len(ACCOUNTS)} total</div>
        <div class="tblwrap">
          <table>
            <thead><tr><th>Account</th><th>Type</th><th>Value</th><th>Holds</th></tr></thead>
            <tbody>{acct_rows}</tbody>
            <tfoot><tr><td class="sym">ALL</td><td class="num"></td>
              <td class="num">{money(all_total)}</td><td class="num"></td></tr></tfoot>
          </table>
        </div>
      </div>

      {agentic_note}

      <div>
        <div class="lbl">Positions · {len(pos)} held across {len(set(p.get("acct","") for p in pos))} account(s) · {EQ_ASOF}</div>
        <div class="tblwrap">
          <table>
            <thead><tr><th>Symbol</th><th>Qty</th><th>Value</th><th>P&amp;L</th><th>%</th></tr></thead>
            <tbody>{posrows}</tbody>
            <tfoot><tr>
              <td class="sym">TOTAL</td><td class="num"></td>
              <td class="num">{money(tot_val)}</td>
              <td class="num {'pos' if tot_pl>=0 else 'neg'}">{signed(tot_pl)}</td>
              <td class="num {'pos' if tot_pl>=0 else 'neg'}">{pct(tot_pl/tot_cost*100 if tot_cost else 0)}</td>
            </tr></tfoot>
          </table>
        </div>
      </div>
    </div>
  </div>

  <!-- 3 ─ Zillow -->
  <div class="card">
    <div class="stripe {'ok' if zill_ok else 'bad'}"></div>
    <div class="head">
      <h2>Zillow market scanner</h2>
      {chip('loaded' if zill_ok else 'not loaded', 'ok' if zill_ok else 'bad')}
      {chip('daily 7:30 am', 'acc')}
      <span class="sid">~/zillow-agent</span>
    </div>
    <div class="body">
      <div class="kv">
        <div><span class="k">Listings</span><span class="v">{len(zs.get("listings",{})):,}</span></div>
        <div><span class="k">ZIPs</span><span class="v">{zzips}</span></div>
        <div><span class="k">Markets</span><span class="v">{len(zmk)}</span></div>
        <div><span class="k">Last digest</span><span class="v">{e(age(zs.get("lastRun")))}</span></div>
      </div>
      <div><div class="lbl">Markets</div>
        <ul class="tks">{''.join(f'<li class="tk">{e(m.get("name","?"))}</li>' for m in zmk)}</ul></div>
      <div class="note">
        <span>ℹ</span><span>Zillow's own endpoint returns a <b>PerimeterX captcha</b> and is
        not usable from this machine. Listings come from Redfin's <code>gis-csv</code> via
        bounding-box polygons, filtered to ZIP client-side — that path caps at 350 rows,
        so an adaptive quadtree splits dense boxes to recover the oldest listings.</span>
      </div>
    </div>
  </div>

  <!-- 4 ─ TradingView -->
  <div class="card">
    <div class="stripe ok"></div>
    <div class="head">
      <h2>TradingView</h2>
      {chip('cdp 9222', 'acc')}
      {chip('84 tools', 'acc')}
      <span class="sid">tradesdontlie/tradingview-mcp</span>
    </div>
    <div class="body">
      <div class="kv">
        <div><span class="k">Scanner study</span><span class="v">ejkCnS</span></div>
        <div><span class="k">Timeframe</span><span class="v">30m pinned</span></div>
        <div><span class="k">MCP commit</span><span class="v">c05b8f5</span></div>
        <div><span class="k">Sleep guard</span>
          <span class="v">{'held' if ag_awake['running'] else 'OFF'}</span></div>
      </div>
      <div class="note">
        <span>ℹ</span><span>The notifier reads the chart over CDP directly — it does not use
        the MCP server, but it <b>does</b> borrow that install's <code>ws</code> package.
        Moving or clearing <code>~/tradingview-mcp/node_modules</code> stops the watcher.</span>
      </div>
      <div>
        <div class="lbl">Delayed feeds · CME add-on not held</div>
        <ul class="tks">{''.join(f'<li class="tk warn">{e(s)}</li>' for s in ["MNQ1!","MES1!","MYM1!","MGC1!","MCL1!"])}</ul>
      </div>
    </div>
  </div>

  <!-- 5 &#9472; Scheduled work -->
  <div class="card">
    <div class="stripe {'bad' if unknown_tasks else 'ok'}"></div>
    <div class="head">
      <h2>Scheduled work</h2>
      {chip(f"{len(agents_cfg.get('launchd', []))} agents", 'acc')}
      {chip(f"{len(agents_cfg.get('claude_tasks', []))} tasks", 'acc')}
      <span class="sid">launchd + claude</span>
    </div>
    <div class="body">
      <div class="agrid">
        {''.join(
          f'<span class="n">{e(w)}</span>'
          f'<span class="w">{e(ev)}</span>'
          f'{chip(lbl, kind)}'
          for w, ev, kind, lbl in agent_rows())}
      </div>
      {'' if not unknown_tasks else
       '<div class="note bad"><span>&#9679;</span><span><b>Unrecorded scheduled task(s):</b> '
       + e(', '.join(unknown_tasks))
       + '. They run but are not described in <code>agents.json</code>, so this card cannot say what they do.</span></div>'}
      <div class="note">
        <span>&#8505;</span><span>launchd agents are checked live against
        <code>launchctl</code>. Claude task schedules live in the app rather than on disk,
        so they are recorded in <code>agents.json</code> &#8212; the card cross-checks the task
        directory and flags anything it does not recognise rather than quietly omitting it.</span>
      </div>
    </div>
  </div>

  <footer>
    Built from local state by <code>build-dashboard.py</code>. Equity prices are
    {EQ_ASOF}; crypto and futures trade around the clock and are current. Re-run the
    script to refresh the watcher panels; the RH panel needs a new connector pull.
  </footer>
</div>
"""

SAFE = HTML.encode("ascii", "xmlcharrefreplace").decode("ascii")
with open(OUT, "w", encoding="utf-8") as f:
    f.write(SAFE)
print(f"wrote {OUT} ({len(SAFE):,} bytes)")
print(f"  flip {len(reg)} symbols | kalshi {len(ks.get('markets',{}))} | zillow {len(zs.get('listings',{})):,}")
print(f"  RH {money(tot_val)} value, {signed(tot_pl)} open P&L across {len(pos)} positions")
