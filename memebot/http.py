"""Small HTTP helper with per-host pacing and retries, so free APIs are not hammered."""
import time
from urllib.parse import urlparse

import requests

# Minimum seconds between calls to each host (free-tier friendly).
HOST_GAP = {
    "api.geckoterminal.com": 6.5,   # keyless limit is ~10 calls/min
    "api.dexscreener.com": 1.1,
    "api.rugcheck.xyz": 1.5,
    "api.gopluslabs.io": 2.0,
    "api.telegram.org": 0.5,
}
_last_call = {}
_session = requests.Session()
_session.headers.update({"User-Agent": "memebot-cloud/1.0", "Accept": "application/json"})


def get_json(url, params=None, retries=3, timeout=20):
    host = urlparse(url).hostname or ""
    gap = HOST_GAP.get(host, 1.0)
    for attempt in range(retries):
        wait = gap - (time.time() - _last_call.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        _last_call[host] = time.time()
        try:
            r = _session.get(url, params=params, timeout=timeout)
            if r.status_code == 429:
                time.sleep(10 * (attempt + 1))
                continue
            if r.status_code >= 500:
                time.sleep(3 * (attempt + 1))
                continue
            if r.status_code != 200:
                print(f"[http] {r.status_code} {url}")
                return None
            return r.json()
        except (requests.RequestException, ValueError) as e:
            print(f"[http] error {url}: {e}")
            time.sleep(3 * (attempt + 1))
    return None


def post_json(url, payload, timeout=20):
    try:
        r = _session.post(url, json=payload, timeout=timeout)
        if r.status_code != 200:
            print(f"[http] POST {r.status_code} {url}: {r.text[:300]}")
        return r
    except requests.RequestException as e:
        print(f"[http] POST error {url}: {e}")
        return None
