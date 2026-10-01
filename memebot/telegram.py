"""Telegram alerts."""
import html
import os

from .http import post_json


def send(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        print("[telegram] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set - printing instead:\n" + text)
        return False
    r = post_json(f"https://api.telegram.org/bot{token}/sendMessage",
                  {"chat_id": chat, "text": text, "parse_mode": "HTML",
                   "disable_web_page_preview": True})
    return bool(r is not None and r.status_code == 200)


def money(v):
    if v is None:
        return "?"
    v = float(v)
    if v >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v / 1_000:.0f}k"
    return f"${v:.0f}"


def _pct(v):
    return "?" if v is None else f"{v:.1f}%"


def format_alert(s, sf, prof_label, sc, zone, realert=False):
    e = html.escape
    chain = "SOL" if s["chain"] == "solana" else s["chain"].upper()
    age = s["age_min"]
    age_txt = f"{age:.0f}m" if age is not None and age < 120 else (
        f"{age / 60:.1f}h" if age is not None and age < 2880 else f"{(age or 0) / 1440:.1f}d")
    ratio = f"{s['vol_ratio']:.1f}" if s["vol_ratio"] else "?"
    head = "🔁 RE-ALERT (cheaper than first alert)\n" if realert else ""
    lines = [
        f"{head}🟢 <b>{chain} · {prof_label} · score {sc}</b>",
        f"<b>${e(s['symbol'])}</b> — {e(s['name'][:40])}   age {age_txt} · {e(s['dex'])}",
        f"MC {money(s['mcap'])} · Liq {money(s['liquidity'])} · Vol 5m {money(s['vol_m5'])} · "
        f"1h {money(s['vol_h1'])} (ratio {ratio}) · 24h {money(s['vol_h24'])}",
        f"✅ LP locked {_pct(sf['lp_locked_pct'])}  ✅ mint/freeze revoked  "
        f"{'✅' if s['dex_paid'] else '⚠️'} DEX paid  {'✅' if s['n_socials'] else '⚠️'} socials",
        f"✅ Top10 {_pct(sf['top10'])}  ✅ max wallet {_pct(sf['top1'])}"
        + (f"  ✅ insiders {_pct(sf['insiders'])}" if sf['insiders'] is not None else "")
        + (f"  · tax {sf['buy_tax']:.0f}/{sf['sell_tax']:.0f}%" if s["chain"] != "solana" else ""),
    ]
    if sf.get("warn"):
        lines.append("⚠️ " + e(", ".join(sf["warn"][:4])))
    if zone:
        r = zone["retracement_now"]
        if zone["in_zone"]:
            where = "✅ price is IN the zone now"
        elif r < 0.618:
            where = f"⏳ price is above the zone (pulled back {r:.2f}) — wait"
        else:
            where = f"⚠️ price fell below 0.9 ({r:.2f}) — weak"
        lines.append(
            f"📐 Entry zone (0.618–0.786): MC {money(zone['zone_bottom_mcap'])}–{money(zone['zone_top_mcap'])}"
            f" · {where}")
        if zone.get("first_candle_share") and zone["first_candle_share"] > 0.5:
            lines.append("⚠️ First candle made >50% of the move — possible bundled launch")
    else:
        lines.append("📐 Entry zone: check chart (pullback below 0.618 of launch→high)")
    lines += [
        "Plan: wait for zone + green candle close · SL −50% · TP1 2x sell 60–80% · keep 10–25% moon bag",
        f"CA: <code>{e(s['token'])}</code>",
    ]
    links = [f"<a href=\"{e(s['url'])}\">DexScreener</a>"] if s.get("url") else []
    if s["chain"] == "solana":
        links.append(f"<a href=\"https://rugcheck.xyz/tokens/{s['token']}\">RugCheck</a>")
        links.append(f"<a href=\"https://app.bubblemaps.io/sol/token/{s['token']}\">Bubblemaps</a>")
    else:
        links.append(f"<a href=\"https://gopluslabs.io/token-security/56/{s['token']}\">GoPlus</a>")
        links.append(f"<a href=\"https://app.bubblemaps.io/bsc/token/{s['token']}\">Bubblemaps</a>")
    if s.get("x"):
        links.append(f"<a href=\"{e(s['x'])}\">X</a>")
    lines.append(" · ".join(links))
    lines.append("<i>Not financial advice. Check the chart before buying.</i>")
    return "\n".join(lines)
