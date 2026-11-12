# Sendwise - Handover Brief

Everything a new engineer (human or AI) needs to take this project over.
Read this first, then `SENDWISE_DOCUMENTATION.md` (product detail), then `DEPLOY.md`.

Repo: `git@github.com:Nazmul-Ahammed/sendwise.git` (branch `main`, private)

---

## 1. What this is

Sendwise is an email marketing control plane for iBOS (AKIJ Resource). It builds
campaigns, writes copy with an LLM, sends through SendClean, tracks opens and clicks
with its own pixel, and runs multi-step lifecycle sequences whose branches depend on
each recipient's real behaviour.

| Layer | Tech | Where |
|---|---|---|
| Frontend | Next.js 14 (App Router, React 18) | port 3000 |
| Backend | Python FastAPI, SQLAlchemy | port 8000 |
| Database | PostgreSQL 16 | port 5432 (docker `sendwise-db`) |
| Edge | OpenResty (Openship managed) | 80/443 |
| Mail | SendClean SMTP relay (`smtp.sendclean.net:587`) | external |

The browser calls `https://sendwise.ibos.agency/api/...` (same origin). The edge routes
`/api/` to the API container and `/` to the frontend container. There is no Next.js
rewrite layer, so `NEXT_PUBLIC_API_URL` is baked into the frontend build.

---

## 2. Live environment (Openship self-hosted)

| Thing | Value |
|---|---|
| Organization | `org_xXTwpr2GF2Trhn8lq05bNFWwCJhDqJOR` |
| Server | `93f943ec-6e99-44e0-8dd8-4625950764ba` (host `ibos-34`, private IP 10.209.99.62) |
| API project | `proj_p2jAfJc0U4DW3Uk5` (host port 20031 -> container 8000) |
| Frontend project | `proj_lAjmSGALeMjHO0x4` (host port 20032 -> container 3000) |
| Cron job | `custom:CcH3_JwqOT_IZwEL`, `*/15 * * * *`, POSTs `/api/cron/sequence-dispatch` |
| Uvicorn | `uvicorn app.main:app --host 0.0.0.0 --port 8000` (ONE worker, no `--workers`) |

### Domain state (read this before touching routing)

`sendwise.ibos.agency` currently resolves to `103.197.205.34`, which answers nothing
(ping, 80 and 443 all dead from every external node tested). The application itself is
healthy. The same app is served correctly through `192.144.80.177`, which is where the
other `ibos.agency` sites live:

```
curl -k --resolve sendwise.ibos.agency:443:192.144.80.177 https://sendwise.ibos.agency/api/health
# {"status":"ok","service":"sendwise-api","version":"1.0.0"}
```

Fix (owner action, DNS is on Cloudflare, `hazel/terry.ns.cloudflare.com`):

```
sendwise.ibos.agency  A  192.144.80.177
```

Openship's Cloudflare credential is `invalid`, so it cannot apply this. `aafl.ibos.agency`
points at the same dead IP and has the same problem.

While the domain is unreachable, every unsubscribe link and tracking pixel in already
sent mail is dead. Fix DNS before the next sequence wave.

---

## 3. Data model (backend/app/models.py)

| Table | Purpose |
|---|---|
| `campaigns` | one campaign; `emails_json` (9 rows), `sequence_json` (4 stages), `audience_options`, `gates_json`, `status` |
| `sequence_enrollments` | one journey per (campaign, email). The core of the engine |
| `contacts` | address book; `email_status` (valid/invalid/no_mx/disposable/role/unknown) |
| `audiences` / `audience_members` | lists; member `tags` is JSON |
| `suppressions` | unsubscribes and hard bounces. Checked before every send |
| `engagement_events` | our own opens/clicks (pixel + click redirect) |
| `delivery_status` | SendClean verdicts per `x_unique_id` (today: all `unknown`) |
| `jobs` | operator-visible log (dispatch, launch, warning, report) |
| `settings` | key/value runtime config (SMTP creds, sendclean creds, dispatch lock) |
| `users` / `auth_tokens` | login |
| `send_records` | send ledger. EXISTS IN THIS REPO, NOT YET IN PRODUCTION |

`sequence_enrollments` fields that matter:

```
campaign_slug, email, token (uuid, unsubscribe id)
current_ref      last email sent ("E1".."E9"); "" means nothing sent yet
next_due_at      ISO-8601 UTC string; "" is a fault, never "due now"
sends            number of emails sent to this person
status           active | completed | unsubscribed | suppressed | failed | needs_review | paused
pending_ref      in-flight claim. Non-empty means an interrupted send: park, never retry
last_x_unique_id provider id used for open/click attribution
history_json     [{"ref","sent_at","x_unique_id","opens","clicks","branch"}]
```

---

## 4. Sequence semantics

A campaign has 9 emails in 4 stages. The branch tree comes from `sequence_json`:

```
E1 -> opened: E2 | not opened: E3
E2 -> clicked: E4 | opened: E5 | not opened: E6
E3 -> clicked: E7 | opened: E8 | not opened: E9
E4..E9 -> terminal
```

So one person receives **at most 3 emails**. `sends` should never exceed 3.
Wait between steps comes from the sequence's own stage delay (default 3 days), read by
`wait_after()`; never hardcode it.

---

## 5. Dispatcher contract (backend/app/sequence_engine.py)

This is the heart of the system. Do not simplify it without understanding every rule.

```python
dispatch(db, dry_run=False, limit=300)
```

Rules, in order:

1. **Window** = `status == "active"` **ORDER BY next_due_at ASC, id ASC** LIMIT `limit`.
   The ORDER BY is mandatory. Without it the database returns an arbitrary slice; once
   those rows all sit on a future date every tick reads them, sends nothing, and the
   genuinely due work is never reached. That exact bug stalled E2..E9 for days.
2. **Time budget** 90s (`SEQUENCE_DISPATCH_BUDGET`). Break, leave rows due, next tick.
3. **Hourly budget** 900 (`SEQUENCE_MAX_SENDS_PER_HOUR`). SendClean accepts roughly
   1000/hour and answers `Hourly limit reached` per message once spent; that once
   recorded 1,662 recipients as failed. Stop early, never let the provider refuse.
4. **Interrupted send** (`pending_ref` non-empty) -> `needs_review`, never retried.
   A duplicate email is worse than a missing one.
5. **Missing next_due_at** -> `needs_review`. Never read as "due now".
6. **Campaign missing** -> `paused` if it is in Trash, else `failed`.
7. **Engagement**: only our own `engagement_events` are read. Provider message lookup
   is not used (it hangs 30s+ per call). If an old send has no local tracking and
   cannot be measured, park it (`needs_review`) instead of assuming "not opened".
8. **Next ref**: `initial = not current_ref` -> `"E1"`; otherwise `decide_next_ref()`.
   No next ref means the journey is `completed`.
9. **Wait** belongs to the NEXT email's stage.
10. **Claim then commit, then send.** `pending_ref` is committed before the network
    call. If the process dies mid-send the claim survives and rule 4 parks it.
11. Commit per recipient, so a crash cannot resend the batch.
12. On exception: `_record_failure()` retries up to `MAX_ATTEMPTS` (3) with backoff,
    then `failed`. `pending_ref` is cleared so the retry is a fresh attempt.

One dispatcher at a time: `acquire_dispatch_lock()` / `release_dispatch_lock()`, a
timestamped row with a 900s TTL so a dead tick cannot wedge the scheduler.

---

## 6. Sending stack

Transport is switchable in settings (`send_transport` = `api` or `smtp`). Production uses
**SMTP**.

`_smtp_connection()` caches one connection in `_SMTP_STATE`, validated with `noop()`.
**On any failure `_smtp_send()` closes it and clears the cache.** This is not optional:
a single dead socket previously answered every later recipient with
`(503, Use MAIL before RCPT)` and `cannot read from timed out object`, failing 404
recipients in one campaign.

Tracking and unsubscribe are ours:

```
pixel      GET  /api/track/o/{base64-token}       (records an open)
unsubscribe GET  /api/unsubscribe?token={uuid}    (suppresses, marks enrollment)
```

Both live on the same public domain as the site, so DNS breakage silently kills them.

---

## 7. Email validation (backend/app/email_validator.py)

On import and on demand (`POST /api/audiences/{id}/validate`):

| Status | How | Effect |
|---|---|---|
| `invalid` | regex + length <= 254 | hard block, never sent |
| `no_mx` | DNS-over-HTTPS MX, falls back to A record (RFC 5321 implicit MX) | hard block |
| `unknown` | resolver failed (SERVFAIL/timeout) | **never dropped**, sends |
| `disposable` | static list of ~40 throwaway domains | risky, sends |
| `role` | local part in role list (info, sales, admin...) | risky, sends |
| `valid` | syntax + MX pass | sends |

MX lookups are cached per domain for 6h. Do not add a mailbox-existence probe (SMTP
RCPT) without a clear decision: it is slow, gets us blocked, and catch-all domains make
it meaningless.

---

## 8. Bugs already fixed - do not regress these

| Symptom | Cause | Fix in repo |
|---|---|---|
| E1 sent, E2..E9 never (6,472 due, 0 dispatched) | dispatch window had no `ORDER BY` | `order_by(next_due_at, id)` |
| Time budget never constrained a pass | two `for enr in rows` loops, budget check in the dead one | single loop |
| 1,662 recipients marked failed | no hourly ceiling | `_sends_last_hour` + 900 cap |
| Queued recipients marked `completed` without a send | no `initial -> E1` branch | `initial = not current_ref` |
| 404 failed in one campaign | one dead SMTP socket reused | reset connection on error |
| Every campaign stuck in `approved` with partial enroll | synchronous `/launch` (see open issue 1) | not fixed in production yet |
| "Failed to fetch" / 504 in the UI | long synchronous requests behind edge + Cloudflare | same |

Product copy fixes: Managerium must render inside the approved template
(`brand_docs/managerium-email-template.html`, SHA256 `746fbd7a...`), and Turbo Guard must
keep the approved US$5.55 offer while never claiming named PHP2Shell detection.

---

## 9. OPEN ISSUES - priority order

1. **`/launch` and `/enroll` are synchronous.** They enroll and send E1 inside the HTTP
   request. A 2,000 recipient campaign runs 10+ minutes, Cloudflare/edge cuts it at
   100-300s, the browser gets a 504, the campaign stays `approved` and only part of the
   audience is enrolled. Observed gaps: 280, 116 and 765 recipients across three
   campaigns. The repo already contains the fix (`queue_enrollments()`,
   `run_queued_dispatch()`, 202 + background) but **production is running the old code**.
   Deploying the repo fixes this.
2. **Production has no `send_records` ledger.** The repo has `SendRecord` +
   `delivery_ledger.py`; production predates it. Port it, otherwise per-message proof is
   limited to `last_x_unique_id`.
3. **DNS** (section 2). Site and every tracking/unsubscribe link are dead until fixed.
4. **`delivery_status` is 100% `unknown`.** The UI therefore shows `delivered: 0` and
   `delivery_rate: 0.0%` while claiming `delivery_confirmed: true`. Either fix the
   SendClean webhook/sync or render "not measured" instead of 0.
5. **Frontend "Failed to fetch"** is a raw browser network error surfaced verbatim. Retry
   once and show a human message. Also raise the cron client timeout (Done for cron:
   300s, job timeout 360s).
6. **Audience payload ~3.2 MB** with no server-side pagination or search.
7. **Single uvicorn worker.** Cron work competes with UI requests. Two workers would help
   once memory allows.
8. **`needs_review` enrollments** are parked forever by design. They need an operator
   action (or a dedicated "resume" endpoint).

---

## 10. Run and test

```powershell
# Backend
cd backend
.\.venv\Scripts\Activate.ps1        # or create it: python -m venv .venv
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# Frontend
cd frontend
npm install
npm run dev                          # http://localhost:3000
```

```powershell
# Tests (backend)
cd backend
python -m unittest tests.test_sequence_queue tests.test_sequence_delays `
  tests.test_managerium_template tests.test_turbo_guard `
  tests.test_email_validator_safety tests.test_reports
```

11 test modules exist. `test_sequence_queue` and `test_sequence_delays` are the important
ones: they encode the dispatcher contract above.

---

## 11. Secrets

**Nothing sensitive is in this repo.** `backend/.env`, `backend/.env.local` and
`frontend/.env.local` are git-ignored and stay on the machine that runs the stack. They
hold the database URL, `SECRET_KEY`, SendClean owner id / API token / SMTP credentials and
the OpenAI key. Runtime values also live in the `settings` table (SMTP user + password,
SendClean token encrypted with Fernet derived from `SECRET_KEY`).

If those files are lost, the credentials are still recoverable from the `settings` table
and the OpenShip project environment, but rotate them rather than mining them.

---

## 12. Conventions

- **No em dash (U+2014) anywhere.** Not in copy, comments, docs or identifiers. Use a
  hyphen. This is an explicit owner instruction.
- Duplicate sends are worse than missing sends. Every "retry" decision starts there.
- Copy doctrine lives in `backend/app/brand_docs/` (`*-brand.md`, `email-writing-skill-v4.md`).
  Product facts come from those files, never invented.
- Facts shown to an operator must be labelled: "relay accepted" is not "delivered".

---

## 13. First tasks for the new owner

1. Fix DNS to `192.144.80.177`, confirm `/api/health` returns `ok` on the public domain.
2. Deploy this repo to the API project so the async launch queue is live. Verify a large
   launch returns 202 quickly and the campaign flips to `live`.
3. Port `SendRecord` / `delivery_ledger` to production and reconcile `delivery_status`.
4. Add one retry + a friendly message to the frontend fetch wrapper.
5. Then and only then, reopen the optimization backlog (audience pagination, workers,
   needs_review tooling).

Ask the owner before: changing DNS, sending to a new audience, rotating credentials, or
re-running any campaign that already partly sent.
