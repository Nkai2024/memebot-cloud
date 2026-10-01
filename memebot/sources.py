"""Discovery (which tokens to look at) and market data (DexScreener)."""
import time

from .http import get_json

GECKO = "https://api.geckoterminal.com/api/v2"
DEXS = "https://api.dexscreener.com"

# Quote/stable tokens that are never the meme coin itself.
IGNORE_TOKENS = {
    "solana": {
        "So11111111111111111111111111111111111111112",  # wSOL
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
        "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    },
    "bsc": {
        "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",  # WBNB
        "0x55d398326f99059ff775485246999027b3197955",  # USDT
        "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",  # USDC
        "0xe9e7cea3dedca5984780bafc599bd69add087d56",  # BUSD
    },
}


def _norm(chain, addr):
    return addr.lower() if chain != "solana" else addr


def _gecko_pool_tokens(chain, path):
    """Return base-token addresses from a GeckoTerminal pools list."""
    data = get_json(f"{GECKO}/networks/{chain}/{path}")
    out = []
    for p in (data or {}).get("data", []) or []:
        rel = p.get("relationships", {})
        for side in ("base_token", "quote_token"):
            tid = ((rel.get(side) or {}).get("data") or {}).get("id", "")
            if "_" in tid:
                addr = tid.split("_", 1)[1]
                if _norm(chain, addr) not in {_norm(chain, a) for a in IGNORE_TOKENS.get(chain, set())}:
                    out.append(addr)
                    break
    return out


def discover(chain, profiles_enabled):
    """Collect candidate token addresses for one chain from several free feeds."""
    found = []
    # Fresh pools (fresh / migrated profiles)
    if profiles_enabled & {"migrated", "fresh_aggressive"}:
        found += _gecko_pool_tokens(chain, "new_pools")
    # Trending pools (swing survivors + migrated that are gaining traction)
    found += _gecko_pool_tokens(chain, "trending_pools")
    # Tokens that just paid for a DexScreener profile ("DEX paid") or bought boosts
    for path in ("/token-profiles/latest/v1", "/token-boosts/latest/v1", "/token-boosts/top/v1"):
        items = get_json(DEXS + path) or []
        if isinstance(items, dict):
            items = items.get("data", []) or []
        for it in items:
            if it.get("chainId") == chain and it.get("tokenAddress"):
                found.append(it["tokenAddress"])
    # de-duplicate, keep order
    seen, uniq = set(), []
    ignore = {_norm(chain, a) for a in IGNORE_TOKENS.get(chain, set())}
    for a in found:
        k = _norm(chain, a)
        if k not in seen and k not in ignore:
            seen.add(k)
            uniq.append(a)
    return uniq


def market_data(chain, token_addresses):
    """DexScreener batch lookup -> {token_address: best_pair_dict} (highest liquidity pair)."""
    best = {}
    for i in range(0, len(token_addresses), 30):
        chunk = token_addresses[i:i + 30]
        pairs = get_json(f"{DEXS}/tokens/v1/{chain}/{','.join(chunk)}") or []
        if isinstance(pairs, dict):
            pairs = pairs.get("pairs", []) or []
        for p in pairs:
            tok = (p.get("baseToken") or {}).get("address")
            if not tok:
                continue
            key = _norm(chain, tok)
            liq = ((p.get("liquidity") or {}).get("usd")) or 0
            if key not in best or liq > (((best[key].get("liquidity") or {}).get("usd")) or 0):
                best[key] = p
    return best


def snapshot_from_pair(chain, p, now=None):
    """Turn a DexScreener pair into the flat fields the scorer uses."""
    now = now or time.time()
    vol = p.get("volume") or {}
    txns = p.get("txns") or {}
    info = p.get("info") or {}
    socials = {s.get("type"): s.get("url") for s in (info.get("socials") or []) if s.get("type")}
    websites = [w.get("url") for w in (info.get("websites") or []) if w.get("url")]
    created = p.get("pairCreatedAt")
    age_min = (now - created / 1000) / 60 if created else None
    vol_m5 = vol.get("m5") or 0
    vol_h1 = vol.get("h1") or 0
    h24 = txns.get("h24") or {}
    return {
        "chain": chain,
        "token": (p.get("baseToken") or {}).get("address"),
        "symbol": (p.get("baseToken") or {}).get("symbol") or "?",
        "name": (p.get("baseToken") or {}).get("name") or "?",
        "pair": p.get("pairAddress"),
        "dex": (p.get("dexId") or "").lower(),
        "url": p.get("url"),
        "price_usd": float(p.get("priceUsd") or 0),
        "mcap": p.get("marketCap") or p.get("fdv") or 0,
        "liquidity": (p.get("liquidity") or {}).get("usd") or 0,
        "vol_m5": vol_m5,
        "vol_h1": vol_h1,
        "vol_h24": vol.get("h24") or 0,
        "txns_h24": (h24.get("buys") or 0) + (h24.get("sells") or 0),
        "change_h24": (p.get("priceChange") or {}).get("h24"),
        "age_min": age_min,
        "dex_paid": bool(info.get("imageUrl") or socials or websites),
        "boosts": (p.get("boosts") or {}).get("active") or 0,
        "x": socials.get("twitter"),
        "telegram": socials.get("telegram"),
        "website": websites[0] if websites else None,
        "n_socials": len(socials) + (1 if websites else 0),
        "vol_ratio": (vol_h1 / vol_m5) if vol_m5 else None,
    }
