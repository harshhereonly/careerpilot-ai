# CareerPilot AI

AI-assisted internship outreach and career management. The AI prepares research, matches and
drafts; **you review and approve every email; only then does the system send it.**

> Current state: **Phase 0 is complete and Phase 1 (database, authentication, backend foundation) is
> built and verified on a real Supabase project, except for a token really issued by Supabase Auth.**
> There are no product features yet. See [`BUILD_STATUS.md`](BUILD_STATUS.md) for exactly what is built and verified, and
> [`ARCHITECTURE.md`](ARCHITECTURE.md) for the design decisions.

## Repository layout

```text
backend/     FastAPI + SQLAlchemy + Alembic (Python); Celery comes with the first background job
frontend/    Next.js App Router + TypeScript + Tailwind + shadcn/ui
extension/   Chrome extension (WXT) - not started, see extension/README.md
docker/      Optional local Postgres + Redis (docker-compose.yml)
scripts/     Developer scripts: check.ps1 (quality gate), test-db.ps1, check-env.ps1
docs/        DATABASE.md, SUPABASE_SETUP.md
```

## Prerequisites

- Python 3.12+ (developed on 3.14.4)
- Node.js 20+ and npm (developed on Node 24 / npm 11)
- A Supabase project: [docs/SUPABASE_SETUP.md](docs/SUPABASE_SETUP.md)

Docker is optional. The database tests run on PGlite, a real PostgreSQL compiled to WebAssembly, which
needs only Node.

## First-time setup (Windows PowerShell)

```powershell
# Backend
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
cd ..

# Frontend
cd frontend
npm install
cd ..

# Configuration: real values go in .env, which is gitignored. Never put them in .env.example.
Copy-Item .env.example .env
```

Then follow [docs/SUPABASE_SETUP.md](docs/SUPABASE_SETUP.md), check the result, and apply the schema:

```powershell
powershell -File scripts/check-env.ps1
backend\.venv\Scripts\python -m app.cli.verify_supabase --migrate
```

## Run

```powershell
# API on http://localhost:8000  (interactive docs at /docs outside production)
backend\.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --reload --port 8000

# Web app on http://localhost:3000
npm --prefix frontend run dev
```

## API so far

| Route | Auth | Purpose |
| --- | --- | --- |
| `GET /api/v1/health` | none | The process is up |
| `GET /api/v1/health/ready` | none | Dependencies are reachable (200 or 503, per-check status) |
| `GET /api/v1/me` | bearer token | The caller, provisioned on first use and read through RLS |

Errors always look like `{"error": {"code", "message", "request_id", "details"?}}`.

## Tests and quality gate

```powershell
powershell -File scripts/check.ps1                 # everything below
powershell -File scripts/test-db.ps1               # only the database tests, on a throwaway Postgres
powershell -File scripts/test-db.ps1 -k rls -x     # extra arguments go to pytest
```

`check.ps1` runs `ruff`, `ruff format --check`, `mypy --strict`, `pytest` (with and without a database),
`eslint`, `prettier --check` and `tsc`. Use `-SkipBackend`, `-SkipDatabase` or `-SkipFrontend` to narrow it.

To run the database tests against your Supabase development project instead, set `TEST_DATABASE_URL`
and run `pytest tests/db` from `backend\`.

## Local containers (optional)

```powershell
docker compose --env-file .env -f docker/docker-compose.yml up -d
```

Set `POSTGRES_PASSWORD` in `.env` first; compose refuses to start without it. This file has only been
syntax-checked (`docker compose config`): Docker's engine does not start on the development machine,
see "Known issues" in `BUILD_STATUS.md`.
