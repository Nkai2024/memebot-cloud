"""Win back paper trades that were lost when state/seen.json got corrupted.

Why: until 2026-10-28 a new Actions job could start from an old copy of the repo, and
`git pull --rebase -X theirs` then merged two versions of seen.json line by line. That broke
the JSON, state.load() saw "no state", and the paper book restarted from zero (3 times
between Oct 26 and 28). Every older version is still in git history, so this walks it once
and merges back:
  - closed paper trades of the current version (deduped by trade id)
  - the archived baselines (e.g. v1) if the current copy lost them
  - alert memory, so old coins are not re-alerted as new
Open positions that were dropped at a reset cannot be finished, so they are only counted.

Runs once per paper version (flag: state["recovery"][version]).
"""
import json
import subprocess
import time

STATE_FILE = "state/seen.json"


def merge(state, snapshots, version):
    """Merge older state snapshots into `state` (in place). Pure function, no git.

    `snapshots` = iterable of older state dicts (any order). Returns a small report dict."""
    book = state.setdefault("paper", {"positions": {}, "closed": [], "last_summary_day": ""})
    have = {c.get("id") for c in book.get("closed", [])}
    have_open = set((book.get("positions") or {}).keys())
    added, earliest = [], book.get("started_ts") or time.time()
    last_open = {}  # id -> position, from the newest snapshot that still had it open
    archive = state.setdefault("paper_archive", {})
    alerts = state.setdefault("alerts", {})

    for snap in snapshots:
        p = snap.get("paper") or {}
        if p.get("version") == version:
            if p.get("started_ts"):
                earliest = min(earliest, p["started_ts"])
            for c in p.get("closed", []):
                if c.get("id") and c["id"] not in have:
                    have.add(c["id"])
                    added.append(dict(c, recovered=True))
            for pid, pos in (p.get("positions") or {}).items():
                if pos.get("status") == "open":
                    last_open.setdefault(pid, pos)
        for ver, arch in (snap.get("paper_archive") or {}).items():
            if ver == version:
                continue
            mine = archive.get(ver) or {}
            if len(arch.get("closed", [])) > len(mine.get("closed", [])):
                archive[ver] = arch
        for k, a in (snap.get("alerts") or {}).items():
            alerts.setdefault(k, a)

    closed_ids = have
    lost_open = [pid for pid in last_open if pid not in closed_ids and pid not in have_open]
    book["closed"] = sorted(book.get("closed", []) + added, key=lambda c: c.get("close_ts", 0))[-500:]
    book["started_ts"] = earliest
    return {"added_closed": len(added), "lost_open": len(lost_open), "started_ts": earliest}


def _git(*args, cwd):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def history_snapshots(root, days=21):
    """Yield every readable older version of seen.json from the last `days` days (newest first)."""
    if _git("rev-parse", "--is-shallow-repository", cwd=root).strip() == "true":
        subprocess.run(["git", "fetch", "-q", "--unshallow"], cwd=root, capture_output=True)
    shas = _git("log", f"--since={days} days ago", "--format=%H", "--", STATE_FILE, cwd=root).split()
    for sha in shas:
        raw = _git("show", f"{sha}:{STATE_FILE}", cwd=root)
        try:
            yield json.loads(raw)
        except ValueError:
            continue  # the corrupted versions


def run_once(state, root, version):
    """Do the recovery if it has not been done for this version yet. Returns report or None."""
    done = state.setdefault("recovery", {})
    if version in done:
        return None
    report = merge(state, history_snapshots(root), version)
    report["ts"] = time.time()
    done[version] = report
    return report
