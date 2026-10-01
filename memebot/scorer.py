"""Profile gates (market data) + safety gates + 0-100 score, following the Web3 Millionaire playbook."""


def _between(v, rng):
    lo, hi = rng
    return v is not None and lo <= v <= hi


def market_gate(s, prof, allowed_dexes):
    """Cheap checks using DexScreener data only. Returns list of failure reasons ([] = pass)."""
    fails = []
    if s["dex"] not in allowed_dexes:
        fails.append(f"dex {s['dex']} not allowed")
    if not _between(s["age_min"], prof["age_minutes"]):
        fails.append("age")
    if not _between(s["mcap"], prof["market_cap"]):
        fails.append("market cap")
    if s["liquidity"] < prof.get("liquidity_min", 0):
        fails.append("liquidity low")
    if prof.get("liquidity_max") and s["liquidity"] > prof["liquidity_max"]:
        fails.append("liquidity too high (already played out)")
    if s["vol_h24"] < prof.get("volume_h24_min", 0):
        fails.append("24h volume")
    if s["vol_m5"] < prof.get("volume_m5_min", 0):
        fails.append("5m volume")
    rmax = prof.get("vol_ratio_h1_m5_max")
    if rmax is not None and (s["vol_ratio"] is None or s["vol_ratio"] >= rmax):
        fails.append("1h/5m volume ratio")
    if s["txns_h24"] < prof.get("txns_h24_min", 0):
        fails.append("24h txns")
    if prof.get("require_dex_paid") and not s["dex_paid"]:
        fails.append("DEX not paid")
    if s["n_socials"] < prof.get("require_socials", 0):
        fails.append("no socials")
    return fails


def safety_gate(sf, rules):
    """Hard safety gates. Unknown values fail (we never assume safe)."""
    if not sf.get("ok"):
        return [sf.get("error", "safety data unavailable")]
    fails = []
    if sf["honeypot"]:
        fails.append("honeypot")
    if not sf["mint_revoked"]:
        fails.append("mint authority not revoked")
    if not sf["freeze_revoked"]:
        fails.append("freeze authority not revoked")
    if sf["lp_locked_pct"] is None or sf["lp_locked_pct"] < rules["lp_locked_min_pct"]:
        fails.append(f"LP locked {sf['lp_locked_pct']}%")
    if sf["top10"] is None or sf["top10"] > rules["top10_max_pct"]:
        fails.append(f"top10 {sf['top10']}")
    if sf["top5"] is not None and sf["top5"] > rules["top5_max_pct"]:
        fails.append(f"top5 {sf['top5']:.1f}%")
    if sf["top1"] is not None and sf["top1"] > rules["single_wallet_max_pct"]:
        fails.append(f"single wallet {sf['top1']:.1f}%")
    if sf["insiders"] is not None and sf["insiders"] > rules["insiders_max_pct"]:
        fails.append(f"insiders {sf['insiders']:.1f}%")
    if sf["creator_pct"] is not None and sf["creator_pct"] > rules["creator_max_pct"]:
        fails.append(f"creator holds {sf['creator_pct']:.1f}%")
    if sf["buy_tax"] > rules["max_buy_tax_pct"] or sf["sell_tax"] > rules["max_sell_tax_pct"]:
        fails.append("tax too high")
    if sf["holder_data_hidden"]:
        fails.append("holder data hidden (all zeros)")
    if rules.get("fail_on_rugcheck_danger") and sf["danger"]:
        fails.append("danger: " + ", ".join(sf["danger"][:3]))
    return fails


def score(s, sf, prof):
    """0-100 quality score for coins that already passed every hard gate."""
    pts = 0.0
    # Momentum (30)
    r = s["vol_ratio"]
    if r is not None:
        pts += 15 if r < 2 else 10 if r < 4 else 5 if r < 8 else 0
    pts += min(s["vol_h24"] / max(s["mcap"], 1), 1.0) * 10
    pts += 5 if s["vol_m5"] >= 20000 else 2 if s["vol_m5"] >= 5000 else 0
    # Holder distribution (25)
    t10 = sf["top10"] or 100
    pts += 15 if t10 <= 15 else 10 if t10 <= 22 else 5
    t1 = sf["top1"] or 100
    pts += 10 if t1 <= 3 else 6 if t1 <= 5 else 0
    # Paid / boosted / socials (15)
    pts += 6 if s["dex_paid"] else 0
    pts += min(s["n_socials"], 3) * 2
    pts += 3 if s["boosts"] else 0
    # Safety margin (20)
    pts += 8 if (sf["lp_locked_pct"] or 0) >= 99 else 0
    ins = sf["insiders"]
    pts += 6 if ins is None or ins <= 3 else 3 if ins <= 7 else 0
    pts += 6 if not sf["warn"] else 3 if len(sf["warn"]) <= 1 else 0
    # Freshness (10)
    lo, hi = prof["age_minutes"]
    if s["age_min"] is not None and hi > lo:
        pts += 10 * (1 - (s["age_min"] - lo) / (hi - lo))
    return round(max(0, min(100, pts)))
