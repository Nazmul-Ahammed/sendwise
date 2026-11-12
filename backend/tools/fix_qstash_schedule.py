"""One-shot repair for the Sendwise sequence-tick schedule on Upstash QStash.

Why this exists
---------------
The sequence only advances when a due enrollment is "ticked". On this deployment that
tick is supposed to come from a QStash schedule every minute, but the schedule pointed
at `/api/qstash/dispatch`, which returns 404 on BOTH backends - so the tick has never
arrived and every dispatch in the job log is labelled "(manual)". The 1,003 recipients
who received E1 on 28 Oct have their next email due on 31 Oct and will never get it
until this is fixed.

The correct endpoint is `/api/cron/sequence-dispatch` (verified alive: 401 `Bad cron
secret` without the header). It accepts either the `X-Cron-Secret` header or a normal
user bearer token, so this script can use whichever you have.

Usage
-----
    $env:QSTASH_TOKEN   = "<QStash token from console.upstash.com/qstash>"
    $env:SENDWISE_AUTH  = "Bearer <CRON_SECRET-based header value OR admin token>"
    $env:SENDWISE_AUTH_HEADER = "X-Cron-Secret"        # or Authorization (default)
    python tools/fix_qstash_schedule.py --dry-run      # show what it would change
    python tools/fix_qstash_schedule.py                # apply

The script is idempotent: it deletes only schedules that point at a dead endpoint and
re-uses an existing correct one instead of piling up duplicates.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

QSTASH_BASE = os.getenv("QSTASH_URL", "https://qstash-us-east-1.upstash.io").rstrip("/")
TARGET = os.getenv("SENDWISE_DISPATCH_URL",
                   "https://sendwise.ibos.agency/api/cron/sequence-dispatch")
CRON = os.getenv("SENDWISE_DISPATCH_CRON", "* * * * *")
DEAD_MARKERS = ("/api/qstash/dispatch", "/api/cron/dispatch")
CORRECT_MARKERS = ("/api/cron/sequence-dispatch",)


def _req(method, path, token, body=None, extra=None):
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    headers.update(extra or {})
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(QSTASH_BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{method} {path} -> HTTP {exc.code}: "
                           f"{exc.read().decode('utf-8', 'replace')[:300]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{method} {path} -> connection failed: {exc.reason}") from exc
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {"raw": raw}


def list_schedules(token):
    payload = _req("GET", "/v2/schedules", token)
    if isinstance(payload, list):
        return payload
    return payload.get("schedules") or payload.get("data") or []


def schedule_destination(entry):
    """QStash returns the destination under a few different keys depending on version."""
    for key in ("destination", "url", "endpoint"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    token = os.getenv("QSTASH_TOKEN", "").strip()
    auth_value = os.getenv("SENDWISE_AUTH", "").strip()
    auth_header = os.getenv("SENDWISE_AUTH_HEADER", "Authorization").strip()

    if not token:
        print("BLOCKED: QSTASH_TOKEN is not set.")
        print("  console.upstash.com/qstash -> your instance -> REST API / Environment Keys")
        return 2
    if not auth_value:
        print("BLOCKED: SENDWISE_AUTH is not set (the header value the endpoint expects).")
        return 2

    print(f"QStash      : {QSTASH_BASE}")
    print(f"Target      : {TARGET}")
    print(f"Cron        : {CRON}")
    print(f"Auth header : {auth_header}: {'*' * 8}{auth_value[-4:]}")

    existing = list_schedules(token)
    print(f"\nFound {len(existing)} existing schedule(s):")
    correct = None
    dead = []
    for entry in existing:
        dest = schedule_destination(entry)
        sid = entry.get("scheduleId") or entry.get("id") or ""
        cron = entry.get("cron") or ""
        print(f"  {sid}  {cron:12s}  {dest}")
        if any(m in dest for m in CORRECT_MARKERS):
            correct = entry
        elif any(m in dest for m in DEAD_MARKERS):
            dead.append(entry)

    if correct and not dead:
        print("\nNothing to do: a correct schedule already exists.")
        return 0

    plan = []
    for entry in dead:
        sid = entry.get("scheduleId") or entry.get("id")
        plan.append(f"DELETE {sid}  ({schedule_destination(entry)})")
    if not correct:
        plan.append(f"CREATE {CRON} -> {TARGET}")

    print("\nPlan:")
    for line in plan:
        print("  " + line)
    if args.dry_run:
        print("\nDry run: nothing changed.")
        return 0

    for entry in dead:
        sid = entry.get("scheduleId") or entry.get("id")
        _req("DELETE", f"/v2/schedules/{sid}", token)
        print(f"  deleted {sid}")

    if not correct:
        _req("POST", "/v2/schedules/" + TARGET, token,
             body={"headers": {auth_header: auth_value}},
             extra={"Upstash-Cron": CRON})
        print(f"  created schedule -> {TARGET}")

    print("\nDone. Verify with: GET /v2/schedules, and Watch for a 'Sequence dispatch'")
    print("row per minute in the Sendwise job log (it used to say '(manual)' only).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
