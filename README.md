# Sendwise - Agent OS

Autonomous email marketing orchestrator control plane.
**Name:** Sendwise · **Tag:** AGENT OS · **Tagline:** *Every send, on the right path.*

## Stack

| Layer | Tech |
|---|---|
| Frontend | Next.js 14 (App Router, React 18) - `http://localhost:3000` |
| Backend | Python FastAPI - `http://localhost:8000` |
| Database | PostgreSQL 16 (Docker) - `localhost:5432` |

## Login

- Email: `admin@sendwise.io`
- Password: `admin123`

## Run (one command)

```powershell
.\start-all.ps1
```

Or manually:

1. **Database**
   ```powershell
   docker compose up -d db
   ```
2. **Backend**
   ```powershell
   cd backend
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   python -m app.seed          # creates tables + seeds demo data
   uvicorn app.main:app --reload --port 8000
   ```
3. **Frontend**
   ```powershell
   cd frontend
   npm.cmd install
   npm.cmd run dev             # http://localhost:3000
   ```

## API summary

- `POST /api/auth/login` - login, returns token
- `GET  /api/dashboard` - KPIs, chart, stages, campaigns, jobs, thresholds
- `GET  /api/campaigns` · `GET /api/campaigns/{slug}`
- `POST /api/campaigns/{slug}/approve|launch|pause`
- `GET  /api/sequences` · `GET /api/reports` · `GET /api/jobs`
- `POST /api/jobs/{id}/retry` · `GET/PATCH /api/settings`

All `/api/*` (except login/health) require `Authorization: Bearer <token>`.

## Reset demo data

```powershell
cd backend
.\.venv\Scripts\python.exe -c "from app.db import Base, engine; from app import models; Base.metadata.drop_all(bind=engine); Base.metadata.create_all(bind=engine)"
.\.venv\Scripts\python.exe -m app.seed
```

