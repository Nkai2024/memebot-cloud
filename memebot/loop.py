"""Keep one GitHub Actions job running for hours instead of trusting GitHub's cron.

Why: the schedule says "every 5 minutes", but GitHub only actually ran the bot about once
every 4 hours (Oct 1-26: 144 runs). Stop losses set at -50% were filling at around -94%.

What this does, inside a single job (up to ~5.5 hours):
  - full scan (discover -> gates -> safety -> alert)  every SCAN_EVERY seconds   (default 5 min)
  - paper position check (prices only)                every PRICE_EVERY seconds  (default 60 s)
  - save state to the repo (git commit + push)        every SAVE_EVERY seconds   (default 10 min)
The workflow is scheduled hourly with a queue, so when one job ends the next one starts.

Run:  python -m memebot.loop
"""
import os
import subprocess
import time

from . import main as M, telegram

SCAN_EVERY = int(os.environ.get("MEMEBOT_SCAN_EVERY", 300))
PRICE_EVERY = int(os.environ.get("MEMEBOT_PRICE_EVERY", 60))
SAVE_EVERY = int(os.environ.get("MEMEBOT_SAVE_EVERY", 600))
LOOP_MINUTES = float(os.environ.get("MEMEBOT_LOOP_MINUTES", 330))


def push_state():
    """Commit state/seen.json and push. Never raises: a failed push just retries next time."""
    cmds = [
        ["git", "add", "state/seen.json"],
        ["git", "commit", "-q", "-m", "state update [skip ci]"],
        ["git", "pull", "-q", "--rebase", "-X", "theirs"],
        ["git", "push", "-q"],
    ]
    for c in cmds:
        r = subprocess.run(c, cwd=M.ROOT, capture_output=True, text=True)
        if r.returncode != 0 and c[1] == "commit":
            return  # nothing changed
        if r.returncode != 0:
            print(f"[loop] {' '.join(c)} failed: {r.stderr.strip()[:200]}")
            return


def run_loop(minutes=LOOP_MINUTES, dry_run=False, sleep=time.sleep, clock=time.time):
    end = clock() + minutes * 60
    last_scan, last_save = float("-inf"), clock()
    scans = checks = 0
    while clock() < end:
        cfg = M.load_config()  # re-read so config edits apply without restarting
        started = clock()
        try:
            if started - last_scan >= SCAN_EVERY:
                M.run(cfg, dry_run=dry_run)  # also updates paper positions first
                last_scan, scans = started, scans + 1
            else:
                M.check_positions(cfg, dry_run=dry_run)
                checks += 1
        except Exception as ex:  # one bad cycle must never kill the job
            print(f"[loop] cycle error: {ex}")
        if not dry_run and clock() - last_save >= SAVE_EVERY:
            push_state()
            last_save = clock()
        wait = PRICE_EVERY - (clock() - started)
        if wait > 0 and clock() + wait < end:
            sleep(wait)
        elif clock() + max(wait, 0) >= end:
            break
    if not dry_run:
        push_state()
    print(f"[loop] finished: {scans} scans, {checks} price checks")
    return scans, checks


def main():
    if os.environ.get("MEMEBOT_TEST", "").lower() in ("1", "true", "yes"):
        ok = telegram.send("✅ <b>MemeBot test message</b>\nTelegram works. v2 is starting: "
                           "scans every 5 min, paper prices every minute.")
        print("Test message sent" if ok else "Test message FAILED - check the two secrets")
    run_loop()


if __name__ == "__main__":
    main()
