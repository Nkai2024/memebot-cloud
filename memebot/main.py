"""One scan cycle: discover -> market gates -> safety gates -> score -> Telegram alert.

GitHub Actions runs this every few minutes. Alert-only: it never trades or touches a wallet."""
import json
import os
import sys
import time

from . import chart, paper, safety, scorer, sources, state as st, telegram

ROOT = os.path.dirname(os.path.dirname(__file__))


def load_config():
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
        return json.load(f)


def run(cfg, dry_run=False):
    state = st.load()
    first_run = state is None
    state = state or {"alerts": {}, "rejected": {}, "runs": 0}
    st.prune(state, cfg["forget_after_days"])
    state["runs"] = state.get("runs", 0) + 1

    if first_run and not dry_run:
        telegram.send("🤖 <b>MemeBot cloud is live</b>\nWatching: " + ", ".join(cfg["chains"]).upper()
                      + "\nProfiles: " + ", ".join(k for k, p in cfg["profiles"].items() if p.get("enabled"))
                      + "\nAlert-only · never trades.")

    # Paper trading: check existing pretend positions against current prices first
    paper_events = []
    try:
        paper_events += paper.update(state, cfg)
    except Exception as ex:
        print(f"[paper] update error: {ex}")

    profiles = {k: p for k, p in cfg["profiles"].items() if p.get("enabled")}
    alerts_sent, checked, rejected_safety = 0, 0, 0
    rejected = state.setdefault("rejected", {})

    for chain in cfg["chains"]:
        try:
            tokens = sources.discover(chain, set(profiles))
        except Exception as ex:  # one chain failing must not stop the other
            print(f"[{chain}] discovery failed: {ex}")
            continue
        print(f"[{chain}] {len(tokens)} candidate tokens")
        pairs = sources.market_data(chain, tokens)
        allowed = set(cfg["allowed_dexes"].get(chain, []))

        for p in pairs.values():
            if alerts_sent >= cfg["max_alerts_per_run"]:
                break
            s = sources.snapshot_from_pair(chain, p)
            if not s["token"]:
                continue
            key = f"{chain}:{s['token']}"
            # 1) cheap market gates per profile
            matched = [(name, prof) for name, prof in profiles.items()
                       if not scorer.market_gate(s, prof, allowed)]
            if not matched:
                continue
            ok, is_realert = st.should_alert(state, key, s["mcap"], cfg)
            if not ok:
                continue
            # skip tokens that failed safety recently (saves API calls); retry after 6h
            if time.time() - rejected.get(key, 0) < 6 * 3600:
                continue
            checked += 1
            # 2) safety gates
            try:
                sf = safety.check(chain, s["token"], s["pair"])
            except Exception as ex:  # one odd token must never crash the whole scan
                print(f"  ! {s['symbol']} ({chain}) safety check error: {ex}")
                continue
            fails = scorer.safety_gate(sf, cfg["safety"])
            if fails:
                rejected_safety += 1
                if sf.get("ok"):
                    rejected[key] = time.time()
                print(f"  ✗ {s['symbol']} ({chain}) safety: {'; '.join(fails)}")
                continue
            # 3) score and pick best matching profile
            best = max(((scorer.score(s, sf, prof), name, prof) for name, prof in matched),
                       key=lambda t: t[0])
            sc, name, prof = best
            if sc < cfg["min_score"]:
                print(f"  · {s['symbol']} ({chain}) score {sc} < {cfg['min_score']}")
                continue
            zone = None
            try:
                zone = chart.entry_zone(chain, s["pair"], s["price_usd"], s["mcap"],
                                        prof.get("chart_timeframe", "minute"))
            except Exception as ex:
                print(f"  zone error: {ex}")
            msg = telegram.format_alert(s, sf, prof["label"], sc, zone, realert=is_realert)
            if dry_run:
                print("---- DRY RUN ALERT ----\n" + msg)
            elif telegram.send(msg):
                pass
            st.record_alert(state, key, s["mcap"], name, s["symbol"])
            try:
                paper_events += paper.on_alert(state, s, zone, name, prof, cfg)
            except Exception as ex:
                print(f"[paper] open error: {ex}")
            alerts_sent += 1
            print(f"  ✓ ALERT {s['symbol']} ({chain}) {name} score {sc}")

    if paper_events:
        msg = "📝 <b>Paper trades</b> (pretend money)\n" + "\n".join(paper_events)
        print(msg)
        if not dry_run:
            telegram.send(msg)
    try:
        report = paper.summary(state, cfg, force=os.environ.get("MEMEBOT_REPORT", "").lower() in ("1", "true", "yes"))
        if report:
            print(report)
            if not dry_run:
                telegram.send(report)
    except Exception as ex:
        print(f"[paper] summary error: {ex}")

    state["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    state["last_summary"] = {"checked": checked, "rejected_safety": rejected_safety, "alerts": alerts_sent}
    st.save(state)
    print(f"Done. checked={checked} rejected={rejected_safety} alerts={alerts_sent}")
    return alerts_sent


def main():
    cfg = load_config()
    if os.environ.get("MEMEBOT_TEST", "").lower() in ("1", "true", "yes"):
        ok = telegram.send("✅ <b>MemeBot test message</b>\nYour Telegram settings work. "
                           "Real alerts will arrive automatically.")
        print("Test message sent" if ok else "Test message FAILED - check the two secrets")
        if not ok:
            sys.exit(1)
    run(cfg, dry_run="--dry-run" in sys.argv)


if __name__ == "__main__":
    main()
