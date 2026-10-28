"""Offline tests: the whole pipeline with fake API responses (no internet needed).
Run:  python -m pytest -q   (or)   python tests/test_pipeline.py"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memebot import main as M, sources, safety, scorer, state as st, chart, telegram  # noqa: E402

NOW_MS = int(time.time() * 1000)
SOL_TOKEN = "GoodToken1111111111111111111111111111111pump"
BAD_TOKEN = "BadToken22222222222222222222222222222222pump"
BSC_TOKEN = "0x1111111111111111111111111111111111111111"


def pair(chain, token, sym, age_min, mcap, liq, v5, v1, v24, dex, paid=True):
    return {
        "chainId": chain, "dexId": dex, "url": f"https://dexscreener.com/{chain}/{token}",
        "pairAddress": "POOL_" + token[:6],
        "baseToken": {"address": token, "symbol": sym, "name": sym + " coin"},
        "priceUsd": str(mcap / 1e9), "marketCap": mcap, "fdv": mcap,
        "liquidity": {"usd": liq}, "volume": {"m5": v5, "h1": v1, "h24": v24},
        "txns": {"h24": {"buys": 400, "sells": 300}, "h1": {"buys": 120, "sells": 80},
                 "m5": {"buys": 20, "sells": 12}},
        "priceChange": {"m5": 3, "h1": 15, "h6": 30, "h24": 40},
        "pairCreatedAt": NOW_MS - age_min * 60_000,
        "info": ({"imageUrl": "x", "websites": [{"url": "https://site"}],
                  "socials": [{"type": "twitter", "url": "https://x.com/c"}]} if paid else {}),
    }


GOOD_RC = {
    "mintAuthority": None, "freezeAuthority": None, "creator": "DEV",
    "markets": [{"pubkey": "POOL_GoodTo", "liquidityA": "VAULT_A",
                 "lp": {"lpLockedPct": 100}}],
    "topHolders": [{"address": "VAULT_A", "owner": "POOL_GoodTo", "pct": 18.0, "insider": False}]
    + [{"address": f"H{i}", "owner": f"O{i}", "pct": 2.0 - i * 0.1, "insider": i == 3} for i in range(12)],
    "risks": [{"name": "Low amount of holders", "level": "warn"}],
    "score_normalised": 5,
}
BAD_RC = dict(GOOD_RC, mintAuthority="SomeAuthority",
              topHolders=[{"address": "W", "owner": "W", "pct": 25.0, "insider": True}])
GOPLUS_OK = {"result": {BSC_TOKEN: {
    "is_honeypot": "0", "cannot_sell_all": "0", "is_open_source": "1", "is_mintable": "0",
    "buy_tax": "0.01", "sell_tax": "0.02", "owner_address": "0x000000000000000000000000000000000000dead",
    "creator_percent": "0.01",
    "lp_holders": [{"address": "0x000000000000000000000000000000000000dead", "percent": "0.999", "is_locked": 0}],
    "holders": [{"address": "pool_0x1111", "percent": "0.3", "is_locked": 0}]
    + [{"address": f"0xh{i}", "percent": str(0.02), "is_locked": 0} for i in range(10)],
}}}

SENT = []


def fake_get_json(url, params=None, **kw):
    if "geckoterminal" in url and url.endswith("new_pools") and "/solana/" in url:
        return {"data": [{"relationships": {"base_token": {"data": {"id": f"solana_{SOL_TOKEN}"}}}},
                         {"relationships": {"base_token": {"data": {"id": f"solana_{BAD_TOKEN}"}}}}]}
    if "geckoterminal" in url and url.endswith("new_pools") and "/bsc/" in url:
        return {"data": [{"relationships": {"base_token": {"data": {"id": f"bsc_{BSC_TOKEN}"}}}}]}
    if "geckoterminal" in url and "ohlcv" in url:
        # newest first: price fell from high 1.0e-4 back to 6e-5 (0.62 retracement area)
        rows = [[0, 6e-5, 6.1e-5, 5.9e-5, 6e-5, 1]] + [[0, 0, 1.0e-4, 5e-5, 0, 1]] * 10 + [[0, 0, 2e-5, 1e-5, 0, 1]]
        return {"data": {"attributes": {"ohlcv_list": rows}}}
    if "geckoterminal" in url:
        return {"data": []}
    if "dexscreener.com/tokens/v1/solana" in url:
        return [pair("solana", SOL_TOKEN, "GOOD", 45, 150_000, 40_000, 25_000, 60_000, 200_000, "pumpswap"),
                pair("solana", BAD_TOKEN, "BAD", 30, 120_000, 30_000, 20_000, 50_000, 150_000, "pumpswap")]
    if "dexscreener.com/tokens/v1/bsc" in url:
        p = pair("bsc", BSC_TOKEN, "BNBMEME", 50, 300_000, 60_000, 30_000, 90_000, 300_000, "pancakeswap")
        p["pairAddress"] = "pool_0x1111"
        return [p]
    if "dexscreener" in url:
        return []
    if "rugcheck" in url and SOL_TOKEN in url:
        return GOOD_RC
    if "rugcheck" in url and BAD_TOKEN in url:
        return BAD_RC
    if "gopluslabs" in url:
        return GOPLUS_OK
    return None


def setup_module(_=None):
    for mod in (sources, safety, chart):
        mod.get_json = fake_get_json
    telegram.send = lambda text: SENT.append(text) or True
    st.PATH = "/tmp/memebot_test_state.json"
    if os.path.exists(st.PATH):
        os.remove(st.PATH)


def test_full_run():
    setup_module()
    cfg = M.load_config()
    n = M.run(cfg)
    alerts = [s for s in SENT if "score" in s]
    assert n == 2, n                               # GOOD (sol) + BNBMEME (bsc); BAD rejected
    assert any("$GOOD" in a for a in alerts)
    assert any("$BNBMEME" in a for a in alerts)
    assert not any("$BAD<" in a or "$BAD " in a for a in alerts)
    assert any("MemeBot cloud is live" in s for s in SENT)   # first-run message
    assert "Entry zone" in alerts[0]
    # second run: no duplicates
    SENT.clear()
    assert M.run(cfg) == 0


def test_solana_holder_math_excludes_pool():
    setup_module()
    sf = safety.solana_safety(SOL_TOKEN, "POOL_GoodTo")
    assert sf["lp_locked_pct"] == 100
    assert sf["top1"] < 5                           # 18% pool vault excluded
    assert 10 < sf["top10"] < 30
    assert sf["mint_revoked"] and sf["freeze_revoked"]


def test_bad_token_fails_gates():
    setup_module()
    cfg = M.load_config()
    sf = safety.solana_safety(BAD_TOKEN)
    fails = scorer.safety_gate(sf, cfg["safety"])
    assert any("mint" in f for f in fails)
    assert any("single wallet" in f for f in fails)


# ------------------------------------------------------------------ v2 tests
from memebot import paper, loop  # noqa: E402


def test_fading_coin_blocked_by_momentum():
    cfg = M.load_config()
    prof = cfg["profiles"]["migrated"]
    p = pair("solana", SOL_TOKEN, "FADE", 30, 150_000, 40_000, 25_000, 60_000, 200_000, "pumpswap")
    p["priceChange"] = {"m5": -20, "h1": -35, "h6": 10, "h24": 10}
    p["txns"]["h1"] = {"buys": 50, "sells": 120}
    s = sources.snapshot_from_pair("solana", p)
    fails = scorer.market_gate(s, prof, {"pumpswap"})
    assert any("change_h1" in f for f in fails) and any("buy/sell h1" in f for f in fails)
    # same coin, still rising -> passes
    s2 = sources.snapshot_from_pair("solana", pair("solana", SOL_TOKEN, "UP", 30, 150_000, 40_000,
                                                   25_000, 60_000, 200_000, "pumpswap"))
    assert scorer.market_gate(s2, prof, {"pumpswap"}) == []


def _paper_setup(prices):
    cfg = M.load_config()
    sources.market_data = lambda chain, toks: {t: "P" for t in toks}
    sources.snapshot_from_pair = lambda chain, p: {"mcap": prices["mcap"], "liquidity": 50_000}
    return cfg


def test_paper_breakeven_and_zone_peak():
    real_md, real_snap = sources.market_data, sources.snapshot_from_pair
    try:
        prices = {"mcap": 100_000}
        cfg = _paper_setup(prices)
        state = {}
        s = {"chain": "solana", "token": "T1", "symbol": "T1", "mcap": 100_000}
        prof = {"chart_timeframe": "minute"}
        zone = {"zone_top_mcap": 70_000, "zone_bottom_mcap": 55_000, "in_zone": False}
        paper.on_alert(state, s, zone, "migrated", prof, cfg, features={"score": 80})
        book = state["paper"]
        assert book["version"] == "v2"
        z = [p for p in book["positions"].values() if p["strategy"] == "ZONE"][0]
        prices["mcap"] = 60_000                  # ZONE fills
        paper.update(state, cfg)
        assert z["status"] == "open" and z["peak_mcap"] == 60_000   # peak starts at the fill now
        prices["mcap"] = 160_000                  # INSTANT +60%, not yet 2x
        paper.update(state, cfg)
        prices["mcap"] = 99_000                   # back below INSTANT entry -> breakeven stop
        ev = paper.update(state, cfg)
        assert any("breakeven" in e for e in ev), ev
        inst = [c for c in book["closed"] if c["strategy"] == "INSTANT"][0]
        assert inst["pnl_pct"] > -10 and inst["features"]["score"] == 80
    finally:
        sources.market_data, sources.snapshot_from_pair = real_md, real_snap


def test_swing_stop_is_30pct_and_v1_archived():
    real_md, real_snap = sources.market_data, sources.snapshot_from_pair
    try:
        prices = {"mcap": 1_000_000}
        cfg = _paper_setup(prices)
        state = {"paper": {"positions": {}, "closed": [{"pnl": -5, "pnl_pct": -50, "size": 10, "symbol": "OLD",
                                                        "reason": "stop loss -50%", "strategy": "INSTANT",
                                                        "profile": "migrated", "close_ts": 1}],
                           "last_summary_day": ""}}
        paper.on_alert(state, {"chain": "solana", "token": "T2", "symbol": "T2", "mcap": 1_000_000},
                       None, "swing_survivor", {"chart_timeframe": "hour"}, cfg)
        assert state["paper_archive"]["v1"]["stats"]["n"] == 1
        assert state["paper"]["closed"] == []
        prices["mcap"] = 690_000
        ev = paper.update(state, cfg)
        assert any("stop loss -30%" in e for e in ev), ev
        rep = paper.summary(state, cfg, force=True)
        assert "Baseline v1" in rep and "swing_survivor" in rep
    finally:
        sources.market_data, sources.snapshot_from_pair = real_md, real_snap


def test_loop_scans_every_5min_checks_every_minute():
    calls = {"scan": 0, "check": 0}
    real_run, real_check = M.run, M.check_positions
    M.run = lambda cfg, dry_run=False: calls.__setitem__("scan", calls["scan"] + 1)
    M.check_positions = lambda cfg, dry_run=False: calls.__setitem__("check", calls["check"] + 1)
    t = [0.0]
    try:
        loop.run_loop(minutes=30, dry_run=True, sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])
    finally:
        M.run, M.check_positions = real_run, real_check
    assert calls["scan"] == 6, calls          # 30 min / 5 min
    assert 20 <= calls["check"] <= 24, calls  # the other minutes


def test_recover_merges_lost_trades():
    from memebot import recover
    cur = {"paper": {"version": "v2", "started_ts": 300, "closed": [{"id": "c", "close_ts": 3}],
                     "positions": {"o3": {"status": "open"}}},
           "paper_archive": {"v1": {"closed": []}}, "alerts": {"sol:C": {"last_ts": 3}}}
    old1 = {"paper": {"version": "v2", "started_ts": 200, "closed": [{"id": "b", "close_ts": 2}],
                      "positions": {"o2": {"status": "open"}, "z": {"status": "pending"}}},
            "alerts": {"sol:B": {"last_ts": 2}}}
    old0 = {"paper": {"version": "v2", "started_ts": 100, "closed": [{"id": "a", "close_ts": 1}, {"id": "b", "close_ts": 2}],
                      "positions": {"o1": {"status": "open"}}},
            "paper_archive": {"v1": {"closed": [{"id": "x"}] * 5, "stats": {"n": 5}}}}
    v1old = {"paper": {"version": "v1", "closed": [{"id": "old"}]}}
    rep = recover.merge(cur, [old1, old0, v1old], "v2")
    assert [c["id"] for c in cur["paper"]["closed"]] == ["a", "b", "c"]
    assert rep == {"added_closed": 2, "lost_open": 2, "started_ts": 100}
    assert len(cur["paper_archive"]["v1"]["closed"]) == 5
    assert set(cur["alerts"]) == {"sol:B", "sol:C"}


def test_damaged_state_file_falls_back_to_git(tmp_path, monkeypatch):
    good = {"runs": 7, "paper": {"version": "v2"}}
    monkeypatch.setattr(st, "PATH", str(tmp_path / "seen.json"))
    (tmp_path / "seen.json").write_text('{"runs": 1, "broken": ')
    monkeypatch.setattr(st, "last_good_from_git", lambda *a, **k: good)
    assert st.load() == good
    assert st.load() == good  # and the repaired copy was written back


if __name__ == "__main__":
    test_full_run()
    test_solana_holder_math_excludes_pool()
    test_bad_token_fails_gates()
    test_fading_coin_blocked_by_momentum()
    test_paper_breakeven_and_zone_peak()
    test_swing_stop_is_30pct_and_v1_archived()
    test_loop_scans_every_5min_checks_every_minute()
    print("ALL TESTS PASSED")
    print("\n".join(SENT[:3]))
