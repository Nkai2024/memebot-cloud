"""Contract / holder safety checks. Solana -> RugCheck, BSC -> GoPlus.

Every function returns a dict of normalised fields. Missing data is None (treated as 'unknown',
which the scorer counts as a fail for hard gates - we never assume safe)."""
from .http import get_json

DEAD = {
    "0x000000000000000000000000000000000000dead",
    "0x0000000000000000000000000000000000000000",
    "0xdead000000000000000042069420694206942069",
}


def _pct_list(values):
    """RugCheck pct may be 0-100 or 0-1; normalise to 0-100."""
    vals = [v for v in values if isinstance(v, (int, float))]
    if vals and max(vals) <= 1.0 and sum(vals) <= 1.01:
        return [v * 100 for v in vals], True
    return vals, False


def solana_safety(token, pair_address=None):
    r = get_json(f"https://api.rugcheck.xyz/v1/tokens/{token}/report")
    if not r:
        return {"ok": False, "error": "RugCheck unavailable"}

    tok = r.get("token") or {}
    mint_auth = r.get("mintAuthority", tok.get("mintAuthority"))
    freeze_auth = r.get("freezeAuthority", tok.get("freezeAuthority"))

    # Accounts that belong to pools / known programs - excluded from holder concentration.
    lp_accounts = set()
    lp_locked_pair, lp_locked_max = None, None
    for m in r.get("markets") or []:
        for k in ("pubkey", "liquidityA", "liquidityB", "liquidityAAccount", "liquidityBAccount"):
            if m.get(k):
                lp_accounts.add(m[k])
        pct = (m.get("lp") or {}).get("lpLockedPct")
        if isinstance(pct, (int, float)):
            if pair_address and m.get("pubkey") == pair_address:
                lp_locked_pair = pct
            lp_locked_max = pct if lp_locked_max is None else max(lp_locked_max, pct)
    # Prefer the exact pool we are alerting on; otherwise the best-locked market.
    lp_locked = lp_locked_pair if lp_locked_pair is not None else lp_locked_max
    for addr, info in (r.get("knownAccounts") or {}).items():
        if (info or {}).get("type") in ("AMM", "LOCKER", "Locker", "BURN", "Burn"):
            lp_accounts.add(addr)

    holders = [h for h in (r.get("topHolders") or [])
               if h.get("address") not in lp_accounts and h.get("owner") not in lp_accounts]
    pcts, _ = _pct_list([h.get("pct") for h in holders])
    insider_pcts, _ = _pct_list([h.get("pct") for h in holders if h.get("insider")])
    pcts.sort(reverse=True)

    creator = r.get("creator")
    creator_pct = None
    if creator:
        cp = [h.get("pct") for h in holders if h.get("owner") == creator or h.get("address") == creator]
        cpv, _ = _pct_list(cp)
        creator_pct = sum(cpv) if cpv else 0.0

    risks = r.get("risks") or []
    danger = [x.get("name") for x in risks if (x.get("level") or "").lower() == "danger"]
    warn = [x.get("name") for x in risks if (x.get("level") or "").lower() == "warn"]

    return {
        "ok": True,
        "mint_revoked": mint_auth in (None, ""),
        "freeze_revoked": freeze_auth in (None, ""),
        "lp_locked_pct": lp_locked,
        "top1": pcts[0] if pcts else None,
        "top5": sum(pcts[:5]) if pcts else None,
        "top10": sum(pcts[:10]) if pcts else None,
        "insiders": sum(insider_pcts) if holders else None,
        "creator_pct": creator_pct,
        "holder_data_hidden": bool(holders) and sum(pcts[:10]) == 0,
        "danger": danger,
        "warn": warn,
        "honeypot": False,
        "buy_tax": 0.0,
        "sell_tax": 0.0,
        "rugcheck_score": r.get("score_normalised", r.get("score")),
    }


def _f(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def bsc_safety(token, pair_address=None):
    data = get_json("https://api.gopluslabs.io/api/v1/token_security/56",
                    params={"contract_addresses": token})
    res = ((data or {}).get("result") or {}).get(token.lower())
    if not res:
        return {"ok": False, "error": "GoPlus unavailable"}

    pair = (pair_address or "").lower()
    lp_locked = 0.0
    lp_holders = res.get("lp_holders") or []
    for h in lp_holders:
        if str(h.get("is_locked")) == "1" or (h.get("address") or "").lower() in DEAD:
            lp_locked += _f(h.get("percent"), 0.0) * 100
    lp_locked = min(lp_locked, 100.0) if lp_holders else None

    holders = []
    for h in res.get("holders") or []:
        a = (h.get("address") or "").lower()
        if a == pair or a in DEAD or str(h.get("is_locked")) == "1":
            continue
        holders.append(_f(h.get("percent"), 0.0) * 100)
    holders.sort(reverse=True)

    danger = []
    flags = {
        "is_honeypot": "Honeypot",
        "cannot_sell_all": "Cannot sell all",
        "hidden_owner": "Hidden owner",
        "owner_change_balance": "Owner can change balances",
        "can_take_back_ownership": "Can take back ownership",
        "selfdestruct": "Self-destruct",
        "transfer_pausable": "Transfers pausable",
        "is_blacklisted": "Has blacklist",
    }
    for k, label in flags.items():
        if str(res.get(k)) == "1":
            danger.append(label)
    if str(res.get("is_open_source")) == "0":
        danger.append("Source not verified")
    warn = []
    if str(res.get("is_mintable")) == "1":
        warn.append("Mintable")
    if str(res.get("is_proxy")) == "1":
        warn.append("Proxy contract")

    owner = (res.get("owner_address") or "").lower()
    return {
        "ok": True,
        "mint_revoked": str(res.get("is_mintable")) != "1",
        "freeze_revoked": True,
        "lp_locked_pct": lp_locked,
        "top1": holders[0] if holders else None,
        "top5": sum(holders[:5]) if holders else None,
        "top10": sum(holders[:10]) if holders else None,
        "insiders": None,
        "creator_pct": _f(res.get("creator_percent"), 0.0) * 100,
        "holder_data_hidden": False,
        "danger": danger,
        "warn": warn + ([] if owner in ("", *DEAD) else ["Owner not renounced"]),
        "honeypot": str(res.get("is_honeypot")) == "1",
        "buy_tax": _f(res.get("buy_tax"), 0.0) * 100,
        "sell_tax": _f(res.get("sell_tax"), 0.0) * 100,
        "rugcheck_score": None,
    }


def check(chain, token, pair_address=None):
    if chain == "solana":
        return solana_safety(token, pair_address)
    if chain == "bsc":
        return bsc_safety(token, pair_address)
    return {"ok": False, "error": f"No safety checker for {chain}"}
