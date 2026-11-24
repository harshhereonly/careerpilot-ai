# Supabase setup (development project)

Phase 1 uses Supabase Postgres (data, row-level security) and Supabase Auth (tokens). You create
the project because it needs your account and a database password, which should never pass through
chat or tooling. Everything below takes about 10 minutes.

**Use a separate *development* project**, not one you will later hold real data in. The database
tests write to the database named in `TEST_DATABASE_URL` and delete what they create.

## 1. Create the project

1. Dashboard -> **New project**. Name it `careerpilot-dev`, pick the region closest to you.
2. Let the dashboard generate a strong **database password** and save it in a password manager.
   You cannot read it again later (you can reset it).
3. Wait until the project finishes provisioning. Free-tier projects can be paused after a period
   of inactivity; if the database stops answering later, check the dashboard first.

## 2. Collect the values

| Variable | Where to find it | Notes |
| --- | --- | --- |
| `SUPABASE_URL` | Project Settings -> API -> **Project URL** (`https://<ref>.supabase.co`) | Public value. Also used to derive the token issuer and the JWKS URL. |
| `DATABASE_URL` | Top bar **Connect** -> Connection string -> **Session pooler** | Direct connections are IPv6-only unless you buy the IPv4 add-on, so on a normal home network use the Session pooler (port 5432). Do **not** use the Transaction pooler (port 6543) for migrations. Replace `[YOUR-PASSWORD]` with your password and URL-encode special characters (`@` -> `%40`, `#` -> `%23`, `/` -> `%2F`). The `postgresql://` scheme is accepted as is. |
| `TEST_DATABASE_URL` | Same string as `DATABASE_URL` for now | Kept separate so tests can later point at a throwaway database. |
| `SUPABASE_JWT_SECRET` | Project Settings -> JWT Keys | **Leave empty** if the project uses asymmetric signing keys (ES256/RS256): tokens are then verified with the public JWKS and no secret is needed. Fill it only if the project still uses the legacy HS256 shared secret. |
| `SECRETS_ENCRYPTION_KEY` | Generate locally (below) | Encrypts OAuth tokens at rest. Not a Supabase value. |

Generate the encryption key (prints a value; paste it into `.env`, do not share it):

```powershell
backend\.venv\Scripts\python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Phase 2 (frontend) will also need the **publishable key** (`sb_publishable_...`, Project Settings ->
API Keys). It replaces the legacy `anon` key and is safe in browsers only because every table has
row-level security. The **secret key** (`sb_secret_...`, replaces `service_role`) bypasses RLS: it is
not needed yet and must never go in frontend code.

## 3. Put them in `.env`, not `.env.example`

`.env.example` is tracked by git and must only ever hold names; a test fails if a secret lands in it.
`.env` is the file for real values, and git ignores it.

```powershell
Copy-Item .env.example .env        # only if you have not already
notepad .env                        # type the values yourself
git check-ignore -v .env            # must print a rule: .env is gitignored
```

Type the secrets into the file yourself. Do not paste them into chat, issues or screenshots.
`POSTGRES_PASSWORD` and `POSTGRES_PORT` are only for the optional local Docker containers: leave them
empty when you use Supabase.

Then check what is set. The script prints names only, never values:

```powershell
powershell -File scripts/check-env.ps1
```

## 4. Check the connection

This is read-only. It reports what it finds and never prints a secret:

```powershell
backend\.venv\Scripts\python -m app.cli.verify_supabase
```

It checks the settings, the public signing keys, the connection, the Postgres version, Supabase's
roles and `auth.uid()`, and whether our per-request security context works on your project.

## 5. Tell me when it is ready

Say "Supabase ready". I will then apply the migrations (`--migrate`), run the database tests against
your project and report exactly what passed.

## 6. Frontend and sign-in settings (Phase 2)

1. **Publishable key:** Project Settings -> API Keys -> copy the publishable key (`sb_publishable_...`)
   into `frontend/.env.local` as `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`. It is public by design (it ships to
   every browser), so it is fine to share. Never use the secret key (`sb_secret_...`) here.
2. **Where email links go:** Authentication -> URL Configuration. Set the Site URL to `http://localhost:3000`
   and add `http://localhost:3000/auth/callback` to the Redirect URLs. Without this, confirmation and
   password-reset links point at the wrong place. Add your production URL here when you deploy.
3. **Password rules:** Authentication -> Providers -> Email. Keep "Confirm email" on, and set the minimum
   password length to 8 to match the app's own check.

Restart `npm --prefix frontend run dev` after editing `.env.local`.

## What I will and will not do with your project

- Apply the Alembic migrations: 23 tables, RLS policies, grants, triggers, in the `public` schema.
  The migration refuses to run on Postgres older than 15.
- Run tests that create temporary rows with random IDs and `@example.test` emails, then delete them.
  Day-to-day test runs use a throwaway local database instead (`scripts/test-db.ps1`).
- **Not** create Supabase Auth users or sign in as anyone. To check a real token, run
  `backend\.venv\Scripts\python -m app.cli.verify_supabase --token` yourself once you have a signed-in
  session (Phase 2 adds the login page). The input is hidden and the token is never printed.

## Optional hardening

The browser talks to our API, not to Supabase's auto-generated Data API. Once Phase 2 confirms that,
you can switch the Data API off in the project's API settings, so that the tables cannot be reached
that way at all. RLS and the table privileges already deny it; this removes the surface entirely.

## Sources

- [Connect to your database](https://supabase.com/docs/guides/database/connecting-to-postgres)
- [JSON Web Tokens (JWT) and signing keys](https://supabase.com/docs/guides/auth/jwts)
- [Understanding API keys](https://supabase.com/docs/guides/api/api-keys)
