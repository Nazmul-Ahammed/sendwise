"""Cron tick for the Sendwise branched sequence engine.

Called by the Openship scheduled job. Reads CRON_SECRET from the container
environment, so the secret never has to live in the job definition.

    docker exec <container> python /workspace/tools/dispatch_tick.py

Optional env:
    SELF_DISPATCH_URL  default http://127.0.0.1:8000/api/cron/sequence-dispatch
    DISPATCH_DRY_RUN   "1" to run the dispatcher without sending
"""
import os
import urllib.error
import urllib.request

url = os.getenv("SELF_DISPATCH_URL", "http://127.0.0.1:8000/api/cron/sequence-dispatch")
if os.getenv("DISPATCH_DRY_RUN") == "1":
    url += ("&" if "?" in url else "?") + "dry_run=1"

req = urllib.request.Request(url, method="POST")
req.add_header("x-cron-secret", os.getenv("CRON_SECRET", ""))
req.add_header("Accept", "application/json")
try:
    with urllib.request.urlopen(req, timeout=300) as resp:
        print(resp.read().decode()[:800])
except urllib.error.HTTPError as exc:
    print(f"HTTP {exc.code}: {exc.read().decode()[:400]}")
except Exception as exc:  # noqa: BLE001
    print(f"tick failed: {exc}")
