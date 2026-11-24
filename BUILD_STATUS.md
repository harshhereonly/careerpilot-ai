# Build status

Last updated: 2026-11-02, during Phase 1.

Legend: ✅ verified (tested, evidence below) · 🟡 built, **not yet verified** on the real target ·
⬜ not started · ⛔ blocked

## Phase overview

| Phase | Scope | Status |
| --- | --- | --- |
| 0 | Audit, project structure, tooling, apps start | ✅ done |
| 1 | Database, authentication, backend foundation | 🟡 built and verified on the real Supabase project; **only a token really issued by Supabase Auth is still unchecked** |
| 2 | Frontend foundation and dashboard | 🟡 built: auth pages, route protection, app shell, nine pages, real summary endpoint. Lint, types, 24 frontend unit tests, build and backend tests pass. **Real sign-in, redirects and responsiveness not yet tested**: needs `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` in `frontend/.env.local` (docs/SUPABASE_SETUP.md, section 6) |
| 3+ | Product modules, Gmail, AI, extension | ⬜ not scheduled |

## What you need to do next

1. **Approve Phase 2**, or ask for changes to the decisions listed below.
2. **Check the password:** one typed into `.env.example` earlier appeared in the development session. It was
   removed from the file and never committed, but if your database password is still that one, or it is used
   anywhere else, reset it and update `.env`.

## Phase 1 results

Requirements from the brief, against evidence.

| Requirement | Status | Evidence |
| --- | --- | --- |
| Initialize FastAPI | ✅ | App factory in `app/main.py`; real `uvicorn` process started and answered |
| Configure SQLAlchemy | ✅ | `app/core/db.py`, sync engine with psycopg 3 |
| Connect PostgreSQL | ✅ | Supabase PostgreSQL 17 through the session pooler; also PGlite (PostgreSQL 18.6) |
| Configure Alembic | ✅ | Upgrade, downgrade and upgrade again all work on real Postgres; `alembic check` reports no drift |
| Create database models | ✅ | 23 tables, documented in `docs/DATABASE.md` |
| Supabase auth verification | 🟡 | ✅ With locally signed ES256, RS256 and HS256 tokens. ✅ Your project's public keys fetched: one ES256 key. ⬜ A token actually issued by Supabase has not been checked |
| Authorization dependencies | ✅ | `CurrentUser`, `DbSession`; a guard test calls every endpoint without a token |
| User ownership checks | ✅ | `owned_select`, `get_owned_or_404`; hold even on a connection that bypasses RLS |
| RLS policies | ✅ | Applied and tested on Supabase with its real roles, default privileges and `auth.uid()` |
| Health-check endpoints | ✅ | `/api/v1/health`, `/api/v1/health/ready`, including a live check |
| Structured logging | ✅ | JSON lines inspected on a live server; tokens and secrets confirmed absent |
| Centralized exception handling | ✅ | One error shape; SQL, constraint names and exception text confirmed not to leak |
| API versioning | ✅ | `/api/v1/` |

Tests required by the brief:

| Test area | Status | Where |
| --- | --- | --- |
| Database connectivity | ✅ Supabase | `tests/db/test_verify_supabase_db.py`, `test_api_integration.py` |
| Authentication | ✅ | `tests/test_tokens.py`, `tests/test_api_auth.py` |
| Invalid tokens | ✅ | Tampered, wrong key, wrong audience or issuer, `alg=none`, algorithm confusion, malformed, oversized, service-role, anonymous |
| Expired tokens | ✅ | Separate `token_expired` code so clients can refresh |
| Unauthorized requests | ✅ | Missing or malformed header; every endpoint, by a guard test |
| Cross-user data access | ✅ Supabase | `tests/db/test_rls.py`: every table, read, update, delete, insert, reassign, reference by guessed id |
| Database constraints | ✅ Supabase | `tests/db/test_constraints.py`, including the approval rule and deletion behaviour |

### Latest full run (2026-11-02)

| Check | Result |
| --- | --- |
| `ruff check`, `ruff format --check` | Clean (48 files) |
| `mypy --strict` | Clean (45 files) |
| `pytest`, no database | 134 passed |
| `pytest`, database, fresh PGlite | 318 passed, 0 skipped |
| `pytest`, database, **the real Supabase project** | 317 passed, 1 skipped (the downgrade test, which only runs on a local throwaway database). 0 test users left afterwards |
| Frontend `eslint`, `prettier --check`, `tsc` | Clean |

### Do the tests actually catch problems?

Each protection was sabotaged in a test-only plugin, and the suite was required to go red:

| Sabotage | Tests that failed |
| --- | --- |
| Sessions no longer switch to the restricted role | 21 |
| RLS applied to a session's first transaction only | 4 |
| Application ownership filter removed | 2 |
| Token signatures not checked | 25 |

### Live end-to-end check

Real `uvicorn` process, real Postgres, migrations applied, signed token: `/health` 200, `/health/ready` 200
with the database reached, `/me` 401 without a token, with a garbage token and with an expired one, `/me`
200 with a valid token (user provisioned on first request, same user on the second), unknown route 404.
`X-Request-ID`, CORS for the allowed origin and security headers present. The token, the shared secret and the
garbage value were all searched for in the log and were absent.

### Defects found and fixed by testing

| Defect | How it was found |
| --- | --- |
| **The approval rule had a hole.** A NULL `approved_version` made the CHECK evaluate to NULL, and a CHECK passes on NULL, so a draft could be marked approved with no version | `test_approval_timestamp_alone_is_not_enough`, on real Postgres |
| A plain foreign key let a user point a row at another user's record | Schema review; now composite keys, with tests |
| The "every route needs auth" test was passing on nothing, because FastAPI no longer lists included routes in `app.routes` | A companion test caught it; replaced with a behavioural test |
| A failing test could leave a transaction open and hang the whole suite on PGlite | The first sabotage run hung; sessions now always close and tests have a timeout |
| Test suite stalled against the session pooler, leaving 2 test users behind | First run on Supabase; tests now reuse connections and the rows were removed |
| `alembic_version` sat in `public` without RLS, reachable through Supabase's Data API | Review; now locked down |
| Readiness checker ran the RLS check before migrating | End-to-end run of the checker |

### Decisions taken on my recommendation

These were open questions at the end of Phase 0. I went with my recommendations; tell me if you want
any of them changed.

- **API connection:** per-request `authenticated` role, so RLS also guards API queries (D-04).
- **Token verification:** public JWKS. Your project confirmed this works, so no shared secret is needed (D-16).
- **Drafted models:** reviewed and substantially changed rather than kept as they were.
- **Added beyond the brief:** a `user_settings` table for the daily limit and pause switch; `template_key` on
  drafts for "performance by email template"; PGlite as a development tool; `pytest-timeout`.
- **Duplicate protection is strict:** one live first email per contact, for good. Reaching the same person
  again later means cancelling the earlier draft or using a follow-up. Say if you want this relaxed.

## Not verified yet

| Item | Why it matters |
| --- | --- |
| A token really issued by Supabase Auth | Needs a signed-in user: `verify_supabase --token` now, or naturally in Phase 2. Tests use locally signed tokens of the same shape |
| Concurrency and locking | The test suite runs one session at a time; not exercised anywhere yet |

## Security items from the brief, not started

Listed so that nothing is silently skipped. Each belongs to the phase that first needs it.

| Item | Status |
| --- | --- |
| Rate limiting | ⬜ |
| Data export and data deletion endpoints | ⬜ (`ON DELETE` cascades for user deletion are built and tested) |
| File upload validation | ⬜ with resumes |
| Writing audit log entries | ⬜ (table and its append-only rules are built and tested) |
| OAuth token encryption code | ⬜ with Gmail (storage is built and locked down) |
| Daily limits, suppression, pause enforcement at send time | ⬜ with sending (settings and tables are built) |

Celery and Redis are installed, but no worker exists yet and nothing uses Redis. Supabase Storage is not
integrated.

## Known issues and open risks

1. **Session pooler and connection churn:** the first test run against Supabase stalled on a new connection
   while the suite opened one per session. It passed after the tests were changed to reuse connections, as the
   app does. The cause was inferred from that, not proven. Watch for this if a process opens many short-lived
   connections, such as Celery workers.
2. **Docker Desktop's engine does not start on this machine.** The `docker-desktop` WSL distro stays `Stopped`
   and `docker desktop start` times out. No longer blocking: Supabase is the database and PGlite runs the tests.
   The compose file is only syntax-checked.
3. **No backend dependency lock file yet.** A `pip freeze` from Windows would include Windows-only packages.
   Pick the mechanism (uv or pip-tools) before the first deployment.
4. **Re-registering with the same email:** if a Supabase user is deleted and re-created with the same email,
   provisioning hits the unique email on `users` and returns 409. The account deletion flow must remove both.
5. **Dev-mode only:** the browser console shows a `getComputedStyle` error from an anonymous script on
   `next dev`. It does not occur in the production build. Source not attributed.
6. **Versions:** Python 3.14 locally, while the deploy host's Python is not pinned. PGlite runs PostgreSQL 18,
   while Supabase runs 15 or 17. WeasyPrint on Windows needs the GTK/Pango runtime. Celery on Windows needs
   `--pool=solo`.
7. `TestClient` prints a Starlette deprecation warning about `httpx`. Harmless today; revisit on the next bump.
8. `.claude/` (local tool config) is gitignored. An empty `IMPORTANT.txt` in the project root is not part of
   the project and was left out of git.

## Phase 0 results

| Item | Status | Evidence |
| --- | --- | --- |
| Git repository | ✅ | `main` |
| Directory structure | ✅ | Project root is `E:\internship finder` itself rather than a nested `careerpilot-ai/` folder |
| Backend environment and tooling | ✅ | venv on Python 3.14.4; FastAPI 0.162.0, SQLAlchemy 2.1.6, Pydantic 2.14.2, Alembic 1.20.1, psycopg 3.3.6, PyJWT 2.15.1, structlog 26.1.0 |
| Frontend environment and tooling | ✅ | Next.js 16.4.3, React 19.3.0, Tailwind 4, shadcn/ui, lucide-react, recharts, react-hook-form, zod, TanStack Query |
| Frontend builds and starts | ✅ | `next build` succeeded; rendered in the browser pane. `shadcn init` wrote a self-referencing font variable, fixed |
| Chrome extension | ⬜ | Folder and rules only (`extension/README.md`) |
