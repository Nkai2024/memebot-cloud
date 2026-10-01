"""Playbook entry zone: Fibonacci from launch low to high, expressed in market cap.

Discount zone = 0.618-0.786 retracement of the move. Only called for coins we alert on,
to stay inside GeckoTerminal's free ~10 calls/min."""
from .http import get_json

GECKO = "https://api.geckoterminal.com/api/v2"


def entry_zone(chain, pair_address, price_usd, mcap, timeframe="minute"):
    if not pair_address or not price_usd or not mcap:
        return None
    data = get_json(f"{GECKO}/networks/{chain}/pools/{pair_address}/ohlcv/{timeframe}",
                    params={"aggregate": 1, "limit": 1000, "currency": "usd"})
    rows = ((((data or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list")) or []
    if len(rows) < 5:
        return None
    lows = [r[3] for r in rows if r and r[3]]
    highs = [r[2] for r in rows if r and r[2]]
    if not lows or not highs:
        return None
    low, high = min(min(lows), price_usd), max(max(highs), price_usd)
    if high <= low:
        return None
    k = mcap / price_usd  # price -> market cap factor (supply)
    retr_now = (high - price_usd) / (high - low)
    z618 = high - 0.618 * (high - low)
    z786 = high - 0.786 * (high - low)
    z90 = high - 0.90 * (high - low)
    return {
        "low_mcap": low * k,
        "high_mcap": high * k,
        "zone_top_mcap": z618 * k,
        "zone_bottom_mcap": z786 * k,
        "deep_mcap": z90 * k,
        "retracement_now": retr_now,
        "in_zone": 0.618 <= retr_now <= 0.9,
        "first_candle_share": _first_candle_share(rows, low, high),
    }


def _first_candle_share(rows, low, high):
    """Share of the whole move made by the very first candle (bundled-launch hint)."""
    first = rows[-1]  # GeckoTerminal returns newest first
    try:
        return max(0.0, (first[2] - first[3]) / (high - low))
    except (TypeError, ZeroDivisionError):
        return None
