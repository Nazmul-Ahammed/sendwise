# Sendwise - Deploy to Vercel (Live)

Two projects on Vercel:

| Project | Root | Runtime |
|---|---|---|
| `sendwise-api` (backend) | `backend/` | Python (FastAPI via Mangum) |
| `sendwise-app` (frontend) | `frontend/` | Next.js |

Database: **Vercel Postgres** (or Neon) - serverless Postgres. The local Docker Postgres is dev-only.

---

## Step 0 - Prerequisites (do once)

1. Create a **GitHub** account and a **Vercel** account (login with GitHub is easiest).
2. Create one **GitHub repository** (e.g. `sendwise`) and push this folder into it.
   ```powershell
   cd sendwise
   git init
   git add .
   git commit -m "Sendwise Agent OS - v2"
   git branch -M main
   git remote add origin https://github.com/<you>/sendwise.git
   git push -u origin main
   ```
3. In Vercel: **Storage → Create → Postgres** (creates `POSTGRES_URL` etc. automatically) - or create a Neon DB and copy its connection string.

## Step 1 - Deploy backend (FastAPI)

1. Vercel → **Add New Project → Import** your GitHub repo.
2. Framework Preset: leave default. **Root Directory: `backend`**.
3. Build command: leave empty. Output: empty.
4. Environment Variables:
   - `DATABASE_URL` = your Postgres connection string + `?sslmode=require`
     (or use Vercel's `POSTGRES_URL` and set `DATABASE_URL=${POSTGRES_URL}&sslmode=require` - Vercel injects linked-storage vars automatically)
   - `SERVERLESS=1`
   - `SEED_ON_STARTUP=0`
   - `ADMIN_EMAIL` = your login email
   - `ADMIN_PASSWORD` = a strong password
   - `SECRET_KEY` = a random string
    - `CORS_ORIGINS=https://sendwise.ibos.agency`
 5. Deploy. The canonical single domain is `https://sendwise.ibos.agency` - both the app and `/api/*` are served from it.
6. **Seed the cloud DB once** (tables + demo data), from your machine:
   ```powershell
   cd backend
   $env:DATABASE_URL="postgresql+psycopg://USER:PASSWORD@HOST:5432/DB?sslmode=require"
   .\.venv\Scripts\python.exe -m app.seed
   ```
 7. Verify: open `https://sendwise.ibos.agency/api/health` → `{"status":"ok",...}`

## Step 2 - Deploy frontend (Next.js)

1. Vercel → **Add New Project → Import** the same repo.
2. **Root Directory: `frontend`**.
3. Environment Variable:
   - `NEXT_PUBLIC_API_URL=https://sendwise.ibos.agency`
 4. Deploy → open `https://sendwise.ibos.agency`, login with the admin email/password from Step 1.
5. Back in the backend project settings, update `CORS_ORIGINS` to the frontend URL if it changed.

## Step 3 - Optional

- Custom domain: Vercel → project → Domains.
- **Email provider**: Sendwise runs on SendClean only. Set the owner ID, API token and SMTP username in
  Settings (or via `PATCH /api/settings`). There is no Mailchimp integration and no provider switch.
- **Existing databases**: run `python tools/migrate_db_sendclean.py` once to drop the legacy Mailchimp
  settings and rename `campaigns.mailchimp_campaign_id` to `campaigns.provider_message_id`. It is idempotent.

## Notes / Caveats

- **Serverless cold starts**: each backend invocation may take a few seconds on the Hobby plan. Fine for a dashboard; heavy for many users.
- **Local dev is unchanged**: `docker compose up -d db` + local `.env` still works.
- `.env` and `.venv` are git-ignored - never commit secrets.
- Alternative backend host (if you want always-on Python without cold starts): **Render / Railway / Fly.io** - same `DATABASE_URL` config, run `uvicorn app.main:app --port 8000`.
