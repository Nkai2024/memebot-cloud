"""Paper trading: pretend to trade every alert with fake money, using real prices, and report results.

No wallet, no keys, no real money. Two strategies run side by side so you can compare them:
  INSTANT - buy at the market cap when the alert fires
  ZONE    - place a pretend limit order in the playbook entry zone (0.618-0.786); buy only if price
            pulls back into it before the order expires

Prices are checked once per bot run (every 5-15 min), so spikes/dips between runs are missed.
Results are an approximation, not a guarantee."""
import time
import uuid

from . import sources

D = "$"  # dollar sign, kept separate so the file is easy to paste into web editors

DEFAULTS = {
    "enabled": True,
    "size_usd": 10,                 # pretend amount per trade
    "buy_cost_pct": 3,              # fees + slippage on buy
    "sell_cost_pct": 3,             # fees + slippage on sell
    "stop_loss_pct": 50,            # playbook: -50%
    # [gain %, % of REMAINING position to sell]. 100 = 2x, 400 = 5x, 900 = 10x
    "take_profits": [[100, 70], [400, 50], [900, 100]],
    "moonbag_stop_at_entry": True,  # after TP1, sell the rest if price falls back to entry
    "zone_order_expiry_hours": {"minute": 2, "hour": 48},
    "zone_cancel_below_pct": 30,    # cancel zone order if price falls 30% under the zone bottom
    "max_hold_hours": {"minute": 72, "hour": 336},
    "rug_liquidity_usd": 1000,      # liquidity below this = treat as rugged (worth 0)
    "missing_runs_to_close": 6,     # no price data this many runs in a row -> close at last price
    "daily_summary_utc_hour": 19,   # 19:00 UTC = 20:00 Cameroon
}


def settings(cfg):
    out = dict(DEFAULTS)
    out.update(cfg.get("paper") or {})
    return out


def _book(state):
    return state.setdefault("paper", {"positions": {}, "closed": [], "last_summary_day": ""})


def _money(v):
    sign = "-" if v < 0 else ""
    v = abs(v)
    return f"{sign}{D}{v / 1e6:.2f}M" if v >= 1e6 else f"{sign}{D}{v / 1e3:.0f}k" if v >= 1e3 else f"{sign}{D}{v:.2f}"


def _mc(v):
    return "?" if not v else (f"{D}{v / 1e6:.2f}M" if v >= 1e6 else f"{D}{v / 1e3:.0f}k")


# ---------------------------------------------------------------- opening
def on_alert(state, s, zone, profile_name, prof, cfg, now=None):
    """Called for every Telegram alert. Opens an INSTANT position and (if a zone exists) a ZONE order."""
    pc = settings(cfg)
    if not pc["enabled"] or not s.get("mcap"):
        return []
    now = now or time.time()
    book = _book(state)
    tf = prof.get("chart_timeframe", "minute")
    events = []
    base = {"chain": s["chain"], "token": s["token"], "symbol": s["symbol"], "profile": profile_name,
            "tf": tf, "created_ts": now, "size": pc["size_usd"], "realized": 0.0, "tp_done": [],
            "missing": 0, "last_mcap": s["mcap"], "peak_mcap": s["mcap"]}
    # Avoid duplicates: one open/pending position per token per strategy
    active = {(p["token"], p["strategy"]) for p in book["positions"].values()}

    if (s["token"], "INSTANT") not in active:
        pid = uuid.uuid4().hex[:8]
        book["positions"][pid] = _fill(dict(base, id=pid, strategy="INSTANT", tp_done=[]), s["mcap"], now, pc)
        events.append(f"📝 INSTANT buy {D}{s['symbol']} {_money(pc['size_usd'])} @ MC {_mc(s['mcap'])}")

    if zone and zone.get("zone_top_mcap") and (s["token"], "ZONE") not in active:
        pid = uuid.uuid4().hex[:8]
        hours = pc["zone_order_expiry_hours"].get(tf, 2)
        book["positions"][pid] = dict(base, id=pid, strategy="ZONE", status="pending", tp_done=[],
                                      zone_top=zone["zone_top_mcap"], zone_bottom=zone["zone_bottom_mcap"],
                                      expires_ts=now + hours * 3600)
        if zone.get("in_zone"):
            _fill(book["positions"][pid], s["mcap"], now, pc)
            events.append(f"📝 ZONE buy {D}{s['symbol']} @ MC {_mc(s['mcap'])} (already in zone)")
        else:
            events.append(f"📝 ZONE order {D}{s['symbol']}: buy if MC enters "
                          f"{_mc(zone['zone_bottom_mcap'])}–{_mc(zone['zone_top_mcap'])} within {hours}h")
    return events


def _fill(p, mcap, now, pc):
    p.update(status="open", entry_ts=now, entry_mcap=mcap,
             units=p["size"] * (1 - pc["buy_cost_pct"] / 100) / mcap)
    p["units_left"] = p["units"]
    return p


# ---------------------------------------------------------------- updating
def _sell(p, frac, mcap, pc):
    units = p["units_left"] * frac
    p["units_left"] -= units
    proceeds = units * mcap * (1 - pc["sell_cost_pct"] / 100)
    p["realized"] += proceeds
    return proceeds


def _close(book, pid, reason, now):
    p = book["positions"].pop(pid)
    p.update(status="closed", close_ts=now, reason=reason,
             pnl=p["realized"] - p["size"], pnl_pct=(p["realized"] / p["size"] - 1) * 100)
    book["closed"].append(p)
    book["closed"] = book["closed"][-500:]
    return p


def update(state, cfg, now=None):
    """Check every pending/open paper position against the current price. Returns event lines."""
    pc = settings(cfg)
    book = _book(state)
    if not pc["enabled"] or not book["positions"]:
        return []
    now = now or time.time()
    events = []

    by_chain = {}
    for p in book["positions"].values():
        by_chain.setdefault(p["chain"], set()).add(p["token"])
    prices = {}
    for chain, toks in by_chain.items():
        try:
            pairs = sources.market_data(chain, list(toks))
        except Exception as ex:
            print(f"[paper] price fetch failed for {chain}: {ex}")
            pairs = {}
        for key, pair in pairs.items():
            snap = sources.snapshot_from_pair(chain, pair)
            prices[(chain, key)] = (snap["mcap"], snap["liquidity"])

    for pid in list(book["positions"]):
        p = book["positions"][pid]
        key = (p["chain"], p["token"].lower() if p["chain"] != "solana" else p["token"])
        mcap, liq = prices.get(key, (None, None))

        if p["status"] == "pending":
            if mcap and p["zone_bottom"] <= mcap <= p["zone_top"]:
                _fill(p, mcap, now, pc)
                events.append(f"📝 ZONE filled {D}{p['symbol']} @ MC {_mc(mcap)}")
            elif now > p["expires_ts"]:
                book["positions"].pop(pid)
                events.append(f"⌛ ZONE order expired {D}{p['symbol']} (price never pulled back)")
            elif mcap and mcap < p["zone_bottom"] * (1 - pc["zone_cancel_below_pct"] / 100):
                book["positions"].pop(pid)
                events.append(f"🚫 ZONE order cancelled {D}{p['symbol']} (fell through the zone)")
            continue

        # ---- open position
        if not mcap:
            p["missing"] += 1
            if p["missing"] >= pc["missing_runs_to_close"]:
                _sell(p, 1.0, p["last_mcap"], pc)
                c = _close(book, pid, "no price data", now)
                events.append(_close_line(c))
            continue
        p["missing"] = 0
        p["last_mcap"] = mcap
        p["peak_mcap"] = max(p["peak_mcap"], mcap)
        mult = mcap / p["entry_mcap"]

        if liq is not None and liq < pc["rug_liquidity_usd"]:
            p["units_left"] = 0  # liquidity gone - cannot sell
            events.append(_close_line(_close(book, pid, "RUGGED (liquidity pulled)", now)))
            continue
        if not p["tp_done"] and mult <= 1 - pc["stop_loss_pct"] / 100:
            _sell(p, 1.0, mcap, pc)
            events.append(_close_line(_close(book, pid, f"stop loss -{pc['stop_loss_pct']}%", now)))
            continue
        for i, (gain, pct) in enumerate(pc["take_profits"]):
            if i not in p["tp_done"] and mult >= 1 + gain / 100:
                _sell(p, pct / 100, mcap, pc)
                p["tp_done"].append(i)
                events.append(f"💰 TP{i + 1} {D}{p['symbol']} ({p['strategy']}) at {mult:.1f}x — sold {pct}% of rest")
        if p["units_left"] <= p["units"] * 1e-6:
            events.append(_close_line(_close(book, pid, "all take-profits hit", now)))
            continue
        if p["tp_done"] and pc["moonbag_stop_at_entry"] and mult <= 1.0:
            _sell(p, 1.0, mcap, pc)
            events.append(_close_line(_close(book, pid, "moon bag stopped at entry", now)))
            continue
        max_h = pc["max_hold_hours"].get(p["tf"], 72)
        if now - p["entry_ts"] > max_h * 3600:
            _sell(p, 1.0, mcap, pc)
            events.append(_close_line(_close(book, pid, f"max hold {max_h}h reached", now)))
    return events


def _close_line(c):
    icon = "🟢" if c["pnl"] > 0 else "🔴"
    return (f"{icon} CLOSED {D}{c['symbol']} ({c['strategy']}) {c['pnl_pct']:+.0f}% "
            f"({_money(c['pnl'])}) — {c['reason']}")


# ---------------------------------------------------------------- reporting
def stats(closed):
    n = len(closed)
    if not n:
        return None
    wins = sum(1 for c in closed if c["pnl"] > 0)
    pnl = sum(c["pnl"] for c in closed)
    staked = sum(c["size"] for c in closed)
    best = max(closed, key=lambda c: c["pnl_pct"])
    worst = min(closed, key=lambda c: c["pnl_pct"])
    return {"n": n, "wins": wins, "win_rate": wins / n * 100, "pnl": pnl, "roi": pnl / staked * 100,
            "best": f"{D}{best['symbol']} {best['pnl_pct']:+.0f}%", "worst": f"{D}{worst['symbol']} {worst['pnl_pct']:+.0f}%",
            "rugs": sum(1 for c in closed if c["reason"].startswith("RUGGED"))}


def summary(state, cfg, now=None, force=False):
    """Once a day (or when forced) build a report message."""
    pc = settings(cfg)
    if not pc["enabled"]:
        return None
    book = _book(state)
    now = now or time.time()
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    hour = time.gmtime(now).tm_hour
    if not force and (book.get("last_summary_day") == day or hour < pc["daily_summary_utc_hour"]):
        return None
    book["last_summary_day"] = day

    lines = [f"📊 <b>Paper trading report</b> — {day}", f"Pretend size {_money(pc['size_usd'])} per trade, "
             f"fees+slippage {pc['buy_cost_pct']}%+{pc['sell_cost_pct']}%"]
    for strat in ("INSTANT", "ZONE"):
        closed = [c for c in book["closed"] if c["strategy"] == strat]
        today = [c for c in closed if time.strftime("%Y-%m-%d", time.gmtime(c["close_ts"])) == day]
        st_all, st_day = stats(closed), stats(today)
        lines.append(f"\n<b>{strat}</b>")
        if not st_all:
            lines.append("  no closed trades yet")
            continue
        if st_day:
            lines.append(f"  Today: {st_day['n']} trades · win {st_day['win_rate']:.0f}% · P/L {_money(st_day['pnl'])}")
        lines.append(f"  All time: {st_all['n']} trades · win {st_all['win_rate']:.0f}% · "
                     f"P/L {_money(st_all['pnl'])} ({st_all['roi']:+.0f}% on money staked)")
        lines.append(f"  Best {st_all['best']} · Worst {st_all['worst']} · Rugs {st_all['rugs']}")
    open_pos = [p for p in book["positions"].values() if p["status"] == "open"]
    pending = [p for p in book["positions"].values() if p["status"] == "pending"]
    if open_pos:
        lines.append("\n<b>Open</b>")
        for p in open_pos[:10]:
            mult = (p["last_mcap"] / p["entry_mcap"]) if p.get("entry_mcap") else 0
            lines.append(f"  {D}{p['symbol']} ({p['strategy']}) {(mult - 1) * 100:+.0f}% now")
    lines.append(f"\nPending zone orders: {len(pending)}")
    lines.append("<i>Pretend money only. Prices checked every 5–15 min, so results are approximate.</i>")
    return "\n".join(lines)
