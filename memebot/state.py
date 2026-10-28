"""Remembers what was already alerted, between GitHub Actions runs (saved to state/seen.json)."""
import json
import os
import subprocess
import time

ROOT = os.path.dirname(os.path.dirname(__file__))
PATH = os.path.join(ROOT, "state", "seen.json")


def load():
    try:
        with open(PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None  # None = first ever run
    except ValueError:
        # A damaged file must never wipe the bot's memory: use the newest readable copy in git.
        good = last_good_from_git()
        if good is not None:
            print("[state] seen.json was unreadable - restored the last good copy from git history")
            save(good)
        return good


def last_good_from_git(max_versions=200):
    rel = os.path.relpath(PATH, ROOT)
    r = subprocess.run(["git", "log", f"-n{max_versions}", "--format=%H", "--", rel],
                       cwd=ROOT, capture_output=True, text=True)
    for sha in r.stdout.split():
        show = subprocess.run(["git", "show", f"{sha}:{rel}"], cwd=ROOT, capture_output=True, text=True)
        try:
            return json.loads(show.stdout)
        except ValueError:
            continue
    return None


def save(state):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1, sort_keys=True)
    os.replace(tmp, PATH)  # all-or-nothing write


def prune(state, days):
    cutoff = time.time() - days * 86400
    alerts = state.get("alerts", {})
    for k in [k for k, v in alerts.items() if v.get("last_ts", 0) < cutoff]:
        del alerts[k]
    rejected = state.get("rejected", {})
    for k in [k for k, ts in rejected.items() if ts < cutoff]:
        del rejected[k]


def should_alert(state, key, mcap, cfg):
    """First alert per token; re-alert only if it got meaningfully cheaper (playbook rule)."""
    prev = state.setdefault("alerts", {}).get(key)
    if not prev:
        return True, False
    cheaper = mcap < prev["first_mcap"] * cfg["realert_if_mcap_drops_below_pct_of_first"] / 100
    waited = time.time() - prev["last_ts"] >= cfg["realert_min_minutes"] * 60
    return (cheaper and waited), True


def record_alert(state, key, mcap, profile, symbol):
    a = state.setdefault("alerts", {})
    prev = a.get(key)
    now = time.time()
    if prev:
        prev["last_ts"] = now
        prev["count"] = prev.get("count", 1) + 1
        prev["last_mcap"] = mcap
    else:
        a[key] = {"first_mcap": mcap, "last_mcap": mcap, "first_ts": now, "last_ts": now,
                  "profile": profile, "symbol": symbol, "count": 1}
