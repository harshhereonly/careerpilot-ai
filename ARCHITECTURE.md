# CareerPilot AI - Architecture

Last updated: 2026-11-02 (Phase 1). Tags: **Decided** (in force), **Proposed** (needs your approval),
**Planned** (decided but not built yet). What is actually built and verified is tracked in
`BUILD_STATUS.md`, not here. The schema is documented in `docs/DATABASE.md`.

## 1. Core principle

AI prepares. The user reviews. The user approves. The system executes. Every decision below
protects that: nothing is sent without explicit approval, and the rule is enforced in several
layers, not just the UI.

## 2. System overview

```text
Browser ──► Next.js (Vercel) ──────────► FastAPI  /api/v1  (Render/Railway)
   │            │  Supabase Auth session        │   │
   │            └───────────────┐               │   ├──► Redis ──► Celery worker
Chrome extension (WXT, MV3) ───┼──► same API    │   │              (send, sync, research,
                               ▼               ▼   ▼               classify, follow-ups)
                         Supabase Auth     Supabase Postgres (RLS) + Storage
External providers, each behind an interface: AI, research, discovery, verification,
email delivery (Gmail), contact capture
```

## 3. Repository layout

```text
backend/
  app/core/         config, db, errors, db_errors, logging, middleware, ownership
  app/models/       one declarative Base, grouped by domain
  app/modules/auth/ tokens (JWT verification), deps (CurrentUser, DbSession), service, router
  app/api/v1/       version router, health
  app/cli/          verify_supabase (readiness checker)
  migrations/       0001 tables, 0002 RLS, privileges, triggers
  tests/            no database; tests/db needs a real Postgres
frontend/           src/app  src/components/ui  src/lib
extension/          (reserved)
docker/             docker-compose.yml (optional)
scripts/            check.ps1, test-db.ps1, check-env.ps1
docs/               DATABASE.md, SUPABASE_SETUP.md
```

## 4. Module map

Backend modules live in `app/modules/<name>/{router,service,schemas,repository}.py`; so far only
`auth` exists. A module may call another only through its `service` functions, and may use external
vendors only through a provider interface.

| Module | Responsibility | Provider interface used |
| --- | --- | --- |
| Authentication | Verify Supabase JWT, resolve current user | - |
| Candidate profile | Profile and verified candidate facts | - |
| Resume management | Upload, parse, skills | Storage |
| Contact management | CRUD, dedupe, status, import | Capture |
| Company research | Research runs and stored summaries | Research |
| Email discovery | Candidate addresses for a contact | Discovery |
| Email verification | Verify an address | Verification |
| AI generation | Prompting, grounding on candidate facts | AI |
| Resume matching | Resume vs. job description | AI |
| Cover letter generation | Letters and PDFs | AI, WeasyPrint |
| Review queue | Draft states and approval | - |
| Gmail integration | OAuth, send, sync | Delivery |
| Reply classification | Label inbound replies | AI |
| Follow-up management | Schedule, cancel on reply | - |
| Opportunity tracking | Opportunities and applications | - |
| Analytics | Aggregates over real records only | - |
| Chrome extension | Companion UI, talks to the API only | - |

## 5. Decisions

**D-01 Monorepo - Decided.** One repository holds frontend, backend and extension, so a
cross-cutting change is one change and the docs live next to the code. Cost: three toolchains;
`scripts/check.ps1` is the single entry point that offsets it.

**D-02 FastAPI owns business logic; Supabase supplies identity, Postgres and storage - Decided.**
The browser never writes business tables directly. The Supabase JS client is used for the auth
session (and later signed storage uploads). Approval, daily limits, suppression and de-duplication
must live in one auditable place that Celery workers share, which rules out putting them in
client-reachable code.

**D-03 Identity comes only from the verified token - Decided, built.** `user_id` is never accepted from
a request body, query string or the extension. `get_current_user` turns a verified JWT into an
`AuthenticatedUser`, and everything else takes the id from there. Two guard tests cover all future
endpoints: one calls every documented operation without a token and requires a 401; the other scans the
OpenAPI schema and fails if any request accepts a `user_id`-like input.

**D-04 Two layers of tenant isolation, with RLS applied to the API too - Decided, built.** A backend
connecting as the table owner bypasses RLS, which would leave RLS protecting only direct Data API
access. So each user request runs in an `RLSSession`: `SET LOCAL ROLE authenticated` plus the token's
claims, so that RLS constrains API queries exactly as it would a direct client. Services also filter by
`user_id` (`owned_select`, `get_owned_or_404`), so a bug in either layer alone exposes nothing. The
privileged `system_session` is kept for provisioning, background jobs and reading stored tokens.

The context is applied in an `after_begin` listener, on every transaction, because `SET LOCAL` ends
with its transaction and a query after `commit()` would otherwise silently run as the owner. The session
refuses to run without claims. Both have regression tests, and sabotaging either makes tests fail.

**D-05 Human approval enforced in depth - Decided; database layer built, the rest Planned.**
1. Database: a CHECK ties approval to the exact version of the text, a trigger bumps the version and
   withdraws approval on any edit, `sent` is final, and an outbound message requires an approved draft
   of the same user. Full table in `docs/DATABASE.md`.
2. Service: sending will accept only `approved` drafts and re-check suppression, duplicates, daily limit
   and the pause switch *at send time*, not only at approval time.
3. API: no endpoint will generate and send in one call; approval is its own authenticated action.
Follow-ups use the same path.

**D-06 Secrets and tokens - Decided; storage built, encryption code Planned.** OAuth and API credentials
are Fernet-encrypted into `integration_secrets`, which has RLS enabled, **no policies**, and all
privileges revoked from the client-facing roles. `key_version` allows key rotation. Settings have no
secret defaults (`SecretStr`, empty when unset). A test fails if a secret is put into `.env.example`.

**D-07 Provider abstraction - Decided; Planned.** `app/providers/<capability>/base.py` holds a Protocol
and DTOs per capability (ai, research, discovery, verification, delivery, capture); vendor adapters sit
beside it and a registry picks one from configuration. Domain modules import only the Protocol, so
replacing Gemini with OpenAI touches one adapter and one config value. Every capability gets a fake for
tests and shared contract tests that each adapter must pass.

**D-08 Module boundaries - Decided.** See section 4. Shared kernel in `app/core`. One declarative `Base`
in `app/models`, because Alembic needs a single metadata; models are data definitions, not behavior.
`core` does not import from `modules`. An import-linter rule will enforce boundaries once there is more
than one module.

**D-09 Contact capture and LinkedIn - Decided.** Capture providers: manual, CSV, and an interface for a
future *authorized* API provider. No DOM-extraction provider exists, not even flagged off, and
`contact_sources.provider` has a CHECK that has no value for one. The extension requests no LinkedIn host
permissions and injects no content scripts; a test over the built manifest will enforce it.
`contacts.profile_url` is a user-typed reference and is never fetched.

**D-10 Background work - Decided; Planned.** Celery + Redis. Tasks are idempotent and carry `user_id`.
Periodic work (due follow-ups, Gmail sync) uses Celery beat. Windows development uses `--pool=solo`;
production is Linux.

**D-11 Conservative outreach by default - Decided; settings built, enforcement Planned.** `user_settings`
holds a daily limit (default 10, hard maximum 100 by CHECK), a pause switch, and follow-up limits.
Suppression list, unsubscribe and bounce handling, follow-ups cancelled when a reply arrives, no tracking
pixels. Contacts come only from manual entry, CSV the user supplies, or authorized APIs; no purchased
lists.

**D-12 Configuration - Decided.** `.env.example` is the single reference, and a test requires every setting
to be listed in it. Local: root `.env` (backend, worker, optional docker) and `frontend/.env.local` (public
values). Production: host dashboards. Real environment variables override files.

**D-13 Tooling - Decided.** Backend: `pyproject.toml` + pip, ruff (lint and format), mypy strict, pytest
with a per-test timeout; Python >=3.12 supported, developed on 3.14.4. Frontend: Next.js 16 App Router,
Tailwind 4, shadcn/ui, ESLint, Prettier, `tsc`. Open: backend lock-file mechanism (BUILD_STATUS).

**D-14 API versioning - Decided, built.** All routes live under `/api/v1/`.

**D-15 Synchronous SQLAlchemy with psycopg 3 - Decided, built.** Route handlers are plain `def` and run in
FastAPI's threadpool. Celery workers are synchronous too, so services are shared without two code paths.
Async would add complexity for no benefit at this scale, and it is a contained change later. Automatic
prepared statements are off, because they break behind a transaction-mode pooler.

**D-16 Token verification - Decided, built.** Tokens are verified locally against the project's public JWKS
(confirmed on the real project: one ES256 key at `/auth/v1/.well-known/jwks.json`), so a request needs no
round trip to Supabase and the server holds no signing secret. HS256 is accepted only if a legacy secret is
configured. Accepted algorithms come from the server's configuration, never from the token, which closes
algorithm-confusion attacks. An unknown `kid` triggers at most one refetch per 30 seconds, so forged tokens
cannot flood Supabase; cached keys survive a failed refresh. `role` must be `authenticated`, and anonymous
sign-ins are refused. If keys are unreachable the API answers 503, not 401, so clients do not log users out.

**D-17 Same-owner foreign keys - Decided, built.** See `docs/DATABASE.md`. `SET NULL (column)` needs
PostgreSQL 15, and migration `0001` refuses to run on anything older.

**D-18 One error shape, nothing leaked - Decided, built.** Every error is
`{"error": {"code", "message", "request_id", "details"?}}`. Clients branch on `code`. Validation errors say
where and why but never echo the submitted value, which may be a password. Database errors map to
403, 409 or 503 without SQL, table or constraint names. Unhandled exceptions become a generic 500, are
logged with the traceback, and still carry CORS headers so browsers show the real error.

**D-19 Structured logging - Decided, built.** structlog, with the standard library routed through it, so
uvicorn and SQLAlchemy share the format. JSON in production, readable locally. Each request gets an id
(returned as `X-Request-ID`, a safe caller-supplied one is kept) and carries `user_id` once known. The access
log has the path only, never headers or query strings. Fields named like secrets are redacted centrally.

**D-20 PGlite for database tests - Decided, built.** Docker does not start on the development machine, and
tests should not depend on a cloud project. `scripts/test-db.ps1` starts PGlite, a real PostgreSQL compiled
to WebAssembly, in memory and needing only Node, and throws it away afterwards. It is real Postgres for
parsing, constraints, triggers, roles and RLS. Differences to remember: it has one backend process, so a
transaction left open blocks other connections (the tests close sessions promptly and have a timeout, and
it means **concurrency and locking are not tested here**), and it runs PostgreSQL 18, while this project's
Supabase database runs 17. Schema changes should also be run against Supabase with `TEST_DATABASE_URL`, as was
done for Phase 1. That run took about nine minutes, against about 25 seconds on PGlite.

## 6. Risks to revisit

- **Local Docker** does not start on the dev machine (BUILD_STATUS). It is no longer a blocker.
- **Gmail OAuth:** Google restricts send/read scopes and limits unverified or "testing"-mode apps
  (including refresh-token lifetime). Confirm the exact constraints against current Google docs
  before building the Gmail phase; do not assume.
- **Third-party terms and cost:** Hunter and the verification provider each have usage limits and
  terms; check them when those integrations are built.
- **Hallucinated claims in generated emails:** mitigated by grounding generation in
  `candidate_facts` and by the mandatory review step, not by trusting the model.
- **WeasyPrint on Windows** needs the GTK/Pango runtime; verify PDF generation early in its phase.
- **Data API exposure:** Supabase exposes `public` through its Data API by default. RLS and privileges
  are designed to make that safe; switching the Data API off is a recommended hardening step once the
  frontend confirms it is not needed.
