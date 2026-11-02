# Database

PostgreSQL 15 or newer (Supabase in development and production). 23 tables: the 21 from the brief plus
two justified below. The schema lives in `backend/app/models/`; the migrations in
`backend/migrations/versions/` are the source of truth for what runs.

## Conventions

| Rule | Why |
| --- | --- |
| Every table has a UUID primary key called `id` | Safe to expose in URLs, no enumeration, generated in app or database |
| Every table except `users` has `user_id` → `users.id`, `ON DELETE CASCADE` | Ownership is explicit, and deleting a user removes all their data |
| References between user tables are **composite**: `(user_id, x_id)` → `parent(user_id, id)` | Foreign-key checks bypass RLS. A plain FK would let a user point a row at another user's record if they guessed its id, which also reveals that it exists. The composite key makes the database refuse it |
| `created_at` and `updated_at` on every table; `updated_at` is set by a trigger | Cannot be forgotten by application code. `activity_logs` is append-only and has no `updated_at` |
| Enumerations are `text` + `CHECK`, not Postgres `ENUM` types | Adding a value is a one-line migration and needs no `ALTER TYPE` |
| Emails and domains are stored lowercase, enforced by `CHECK` | A plain unique index is then enough for case-insensitive duplicate detection |

## Relationships

```text
users
├── candidate_profiles ── candidate_facts
├── user_settings
├── resumes ── resume_skills
├── companies ── company_research
├── contacts ──> companies
│   ├── contact_sources
│   ├── email_verifications
│   ├── conversations ──> outreach_campaigns
│   │   ├── email_messages ──> email_drafts
│   │   └── follow_up_tasks ──> contacts, email_drafts
│   └── email_drafts ──> outreach_campaigns, conversations, opportunities, resumes
│       └── attachments ──> resumes
├── opportunities ──> companies
│   └── applications ──> resumes
├── outreach_campaigns ──> resumes, opportunities
├── suppression_list
├── user_integrations ── integration_secrets
└── activity_logs
```

`├──` is ownership with cascade; `──>` is a reference to another of the same user's tables.

## Tables

"Parents" lists the same-owner references and what happens when the parent is deleted. Every table
also cascades from `users`.

| Table | Purpose | Parents | Notable rules |
| --- | --- | --- | --- |
| `users` | App-level user; `id` equals Supabase `auth.users.id` | | Created by the backend on the first verified request. No FK to `auth.users`, so plain Postgres works for tests |
| `candidate_profiles` | Headline, education, targets | | One per user |
| `candidate_facts` | Atomic verified facts; AI may only assert what is grounded here | profile: cascade | |
| `user_settings` *(added)* | Outreach safety controls | | One per user. Daily limit 0-100 (default 10), pause switch, follow-up delay 1-60 days, max follow-ups 0-5 |
| `resumes` | Uploaded resumes and parsed text | | At most one `is_default` per user |
| `resume_skills` | Skills extracted from a resume | resume: cascade | Unique per resume |
| `companies` | Target companies | | `domain` unique per user |
| `company_research` | Stored research runs | company: cascade | |
| `contacts` | Recruiters and hiring managers | company: clear reference | `email` unique per user (lowercase). `profile_url` is a typed reference and is never fetched |
| `contact_sources` | Where a contact came from | contact: cascade | Provider is `manual`, `csv` or `authorized_api`. There is no scraping provider |
| `email_verifications` | Verification results | contact: cascade | |
| `opportunities` | Internships and jobs | company: clear reference | `url` unique per user, ignoring case. `match_score` 0-100 |
| `applications` | One application per opportunity | opportunity: cascade; resume: clear reference | Unique per opportunity |
| `outreach_campaigns` | Groups of outreach | resume, opportunity: clear reference | |
| `conversations` | One email thread | contact: cascade; campaign: clear reference | Gmail thread unique per user |
| `email_drafts` | AI-prepared email awaiting review | contact: cascade; campaign, conversation, opportunity, resume: clear reference | The approval rule, below |
| `email_messages` | Messages actually sent or received | conversation: cascade; draft: **no action** | Gmail message unique per user. Outbound needs an approved draft |
| `follow_up_tasks` | Scheduled follow-ups | conversation, contact: cascade; draft: clear reference | Sequence number unique per conversation |
| `suppression_list` | Do-not-contact addresses and domains | | Exactly one of email or domain; each unique per user |
| `attachments` | File metadata (the file lives in Storage) | draft: cascade; resume: clear reference | Storage path unique per user |
| `activity_logs` | Append-only audit and usage trail | | Clients can insert and read their own rows, never change or delete them |
| `user_integrations` | Which integrations are connected (no secrets) | | One per provider per user |
| `integration_secrets` *(added)* | Encrypted OAuth and API credentials | integration: cascade | Unreachable by every client-facing role |

**Why the two added tables.** `integration_secrets` keeps tokens out of a table the client can read:
with its own table, "no access" can be enforced by privileges and RLS rather than by remembering to
hide a column. `user_settings` holds the daily limit and pause switch that section 7 of the brief
requires, as typed columns with database-enforced safe ranges rather than free-form JSON.

## The approval rule

Enforced in the database so that no application bug can skip it.

```text
draft ─> pending_review ─> approved ─> sending ─> sent (final)
             │               │  ▲         │
             ▼               │  │         ▼
          rejected           │  └───── failed
          cancelled          │            │
                             ▼            ▼
                 any edit: back to pending_review, approval withdrawn
```

| Rule | Mechanism |
| --- | --- |
| A draft cannot be approved, sending, sent or failed without `approved_at` and `approved_version = version` | `ck_email_drafts_approval_required` |
| An approval covers one exact text: any subject or body change bumps `version` | Trigger `email_drafts_guard` |
| Editing an approved or failed draft withdraws the approval | Same trigger |
| Editing while sending or after sending is refused | Same trigger |
| `version` cannot be set by hand | Same trigger |
| `sent` is final, so an email cannot be re-sent by flipping its status | Same trigger |
| One live first email per contact (rejected and cancelled ones do not count) | Partial unique index `uq_email_drafts_one_initial_per_contact` |
| An outbound message must come from the same user's draft in `sending` or `sent` | `ck_email_messages_outbound_has_draft` and trigger `email_messages_require_approved_draft` |
| A draft with a sent message cannot be deleted on its own | `NO ACTION` foreign key; it is checked at the end of the statement, so deleting the whole contact or user still works |

## Row-level security and privileges

Two independent locks: table privileges decide what a role may ever do, and RLS decides which rows.

| Table | `anon` | `authenticated` privileges | RLS policy for `authenticated` |
| --- | --- | --- | --- |
| The 20 owner tables | none | select, insert, update, delete | `user_id = auth.uid()` for read and write |
| `users` | none | select, update | Own row only. The backend creates and deletes users |
| `activity_logs` | none | select, insert | Own rows only; no update or delete policy or privilege |
| `integration_secrets` | none | **none** | RLS on and **no policy**. Only the backend's privileged connection |
| `alembic_version` | none | none | RLS on and no policy |

Supabase's default privileges grant everything on new tables to `anon` and `authenticated`, so
migration `0002` revokes that first and grants only the above.

### How the API connects

| Session | Runs as | Used for | Isolation |
| --- | --- | --- | --- |
| `RLSSession` (`get_db`) | `authenticated`, with the token's claims | Every user request | RLS **and** explicit `user_id` filters |
| `system_session` | The connection's role (table owner, bypasses RLS) | Provisioning users, background jobs, reading stored tokens | Explicit `user_id` filters only, so its use is kept narrow |

`RLSSession` re-applies `SET LOCAL ROLE authenticated` and the claims at the start of **every**
transaction, including the one after a commit. `SET LOCAL` ends with its transaction, so without this
a query after `commit()` would silently run with the owner's privileges. It also refuses to run at
all without claims. Both behaviours have tests.

## Adding a table

1. Add the model, using `OwnedMixin`, `owner_key` if other tables will reference it, and `owned_fk`.
2. `cd backend`, then `.venv\Scripts\python -m alembic revision --autogenerate -m "add x"` and review the file.
3. In the same migration, add RLS, the policy and the privileges, following `0002`. Forgetting this
   fails `tests/db/test_schema.py`.
4. Add a row to `seed_everything` and `OWNED_MODELS` in `tests/db/factories.py`, which puts the table
   under the cross-user tests. `test_seed_covers_every_owned_table` fails until you do.

## Commands

```powershell
# From backend\ (reads DATABASE_URL from the root .env)
.venv\Scripts\python -m alembic upgrade head       # apply migrations
.venv\Scripts\python -m alembic check              # do the migrations match the models?
.venv\Scripts\python -m alembic upgrade head --sql # print the SQL without a database
```
