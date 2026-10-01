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
        "txns": {"h24": {"buys": 400, "sells": 300}}, "priceChange": {"h24": 40},
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
        p = pair("bsc", BSC_TOKEN, "BNBMEME", 60, 300_000, 60_000, 30_000, 90_000, 300_000, "pancakeswap")
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


if __name__ == "__main__":
    test_full_run()
    test_solana_holder_math_excludes_pool()
    test_bad_token_fails_gates()
    print("ALL TESTS PASSED")
    print("\n".join(SENT[:3]))
