"""Row-level security, table privileges and integrity triggers.

Revision ID: 0002_rls_and_triggers
Revises: 0001_initial_schema
"""

from alembic import op

revision = "0002_rls_and_triggers"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None

# The owner may read and write these through RLS.
OWNED_TABLES = (
    "candidate_profiles",
    "candidate_facts",
    "user_settings",
    "resumes",
    "resume_skills",
    "companies",
    "company_research",
    "contacts",
    "contact_sources",
    "email_verifications",
    "opportunities",
    "applications",
    "outreach_campaigns",
    "email_drafts",
    "email_messages",
    "conversations",
    "follow_up_tasks",
    "suppression_list",
    "attachments",
    "user_integrations",
)
ALL_TABLES = ("users", "activity_logs", "integration_secrets", *OWNED_TABLES)
# activity_logs is append-only and has no updated_at column.
WITH_UPDATED_AT = tuple(table for table in ALL_TABLES if table != "activity_logs")

# Wrapped in SELECT so Postgres evaluates it once per statement instead of once per row.
CURRENT_USER_ID = "(SELECT auth.uid())"


def upgrade() -> None:
    _ensure_supabase_objects()
    _set_privileges()
    _enable_rls()
    _create_updated_at_triggers()
    _create_integrity_triggers()


def _ensure_supabase_objects() -> None:
    """Supabase provides these; create stand-ins only on plain Postgres (tests, local dev)."""
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            CREATE ROLE authenticated NOLOGIN;
          END IF;
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            CREATE ROLE anon NOLOGIN;
          END IF;
          IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'auth') THEN
            CREATE SCHEMA auth;
            GRANT USAGE ON SCHEMA auth TO authenticated, anon;
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'auth' AND p.proname = 'uid'
          ) THEN
            CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS $f$
              SELECT coalesce(
                nullif(current_setting('request.jwt.claim.sub', true), ''),
                nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub'
              )::uuid
            $f$;
          END IF;
        END $$;
        """
    )


def _set_privileges() -> None:
    """Start from nothing, then grant only what each table needs.

    Supabase's default privileges give anon and authenticated everything on new tables. That is
    harmless while RLS denies the rows, but one mistaken policy later would expose data, so the
    table-level grants are a second, independent lock.
    """
    op.execute("GRANT USAGE ON SCHEMA public TO authenticated")
    for table in ALL_TABLES:
        op.execute(f"REVOKE ALL ON {table} FROM anon, authenticated")
    for table in OWNED_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO authenticated")
    # The backend provisions and deletes users on its privileged connection.
    op.execute("GRANT SELECT, UPDATE ON users TO authenticated")
    # Append-only from the client's point of view.
    op.execute("GRANT SELECT, INSERT ON activity_logs TO authenticated")
    # integration_secrets: no grants at all. Only the privileged connection can read tokens.
    # alembic_version: lives in public too, so Supabase's Data API would otherwise expose it.
    op.execute("REVOKE ALL ON alembic_version FROM anon, authenticated")


def _enable_rls() -> None:
    for table in (*ALL_TABLES, "alembic_version"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")

    op.execute(
        "CREATE POLICY users_select_self ON users FOR SELECT TO authenticated "
        f"USING (id = {CURRENT_USER_ID})"
    )
    op.execute(
        "CREATE POLICY users_update_self ON users FOR UPDATE TO authenticated "
        f"USING (id = {CURRENT_USER_ID}) WITH CHECK (id = {CURRENT_USER_ID})"
    )
    for table in OWNED_TABLES:
        op.execute(
            f"CREATE POLICY {table}_owner ON {table} FOR ALL TO authenticated "
            f"USING (user_id = {CURRENT_USER_ID}) WITH CHECK (user_id = {CURRENT_USER_ID})"
        )
    op.execute(
        "CREATE POLICY activity_logs_select_own ON activity_logs FOR SELECT TO authenticated "
        f"USING (user_id = {CURRENT_USER_ID})"
    )
    op.execute(
        "CREATE POLICY activity_logs_insert_own ON activity_logs FOR INSERT TO authenticated "
        f"WITH CHECK (user_id = {CURRENT_USER_ID})"
    )
    # integration_secrets has RLS enabled and deliberately no policy.


def _create_updated_at_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION public.set_updated_at() RETURNS trigger
        LANGUAGE plpgsql SET search_path = '' AS $$
        BEGIN
          NEW.updated_at = now();
          RETURN NEW;
        END $$;
        """
    )
    for table in WITH_UPDATED_AT:
        op.execute(
            f"CREATE TRIGGER trg_{table}_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION public.set_updated_at()"
        )


def _create_integrity_triggers() -> None:
    """The approval rule, enforced where no application bug can skip it.

    An approval covers one exact text: any edit bumps ``version``, and the CHECK on email_drafts
    requires ``approved_version`` to equal it. ``version`` itself only moves through this trigger.
    """
    op.execute(
        """
        CREATE FUNCTION public.email_drafts_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path = '' AS $$
        BEGIN
          IF OLD.status = 'sent' AND NEW.status <> 'sent' THEN
            RAISE EXCEPTION 'a sent draft cannot change status'
              USING ERRCODE = 'check_violation';
          END IF;

          IF NEW.subject IS DISTINCT FROM OLD.subject OR NEW.body IS DISTINCT FROM OLD.body THEN
            IF OLD.status IN ('sending', 'sent') THEN
              RAISE EXCEPTION 'a draft that is sending or sent cannot be edited'
                USING ERRCODE = 'check_violation';
            END IF;
            NEW.version := OLD.version + 1;
            IF OLD.status IN ('approved', 'failed') AND NEW.status = OLD.status THEN
              NEW.status := 'pending_review';
              NEW.approved_at := NULL;
              NEW.approved_version := NULL;
            END IF;
          ELSE
            NEW.version := OLD.version;
          END IF;

          RETURN NEW;
        END $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_email_drafts_guard BEFORE UPDATE ON email_drafts "
        "FOR EACH ROW EXECUTE FUNCTION public.email_drafts_guard()"
    )

    op.execute(
        """
        CREATE FUNCTION public.email_messages_require_approved_draft() RETURNS trigger
        LANGUAGE plpgsql SET search_path = '' AS $$
        BEGIN
          IF NEW.direction = 'outbound' AND NOT EXISTS (
            SELECT 1 FROM public.email_drafts d
            WHERE d.id = NEW.draft_id
              AND d.user_id = NEW.user_id
              AND d.status IN ('sending', 'sent')
              AND d.approved_at IS NOT NULL
          ) THEN
            RAISE EXCEPTION 'an outbound message must come from an approved draft'
              USING ERRCODE = 'check_violation';
          END IF;
          RETURN NEW;
        END $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_email_messages_require_approved_draft "
        "BEFORE INSERT OR UPDATE OF direction, draft_id ON email_messages "
        "FOR EACH ROW EXECUTE FUNCTION public.email_messages_require_approved_draft()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_email_messages_require_approved_draft ON email_messages")
    op.execute("DROP FUNCTION public.email_messages_require_approved_draft()")
    op.execute("DROP TRIGGER trg_email_drafts_guard ON email_drafts")
    op.execute("DROP FUNCTION public.email_drafts_guard()")

    for table in WITH_UPDATED_AT:
        op.execute(f"DROP TRIGGER trg_{table}_updated_at ON {table}")
    op.execute("DROP FUNCTION public.set_updated_at()")

    op.execute("DROP POLICY users_select_self ON users")
    op.execute("DROP POLICY users_update_self ON users")
    op.execute("DROP POLICY activity_logs_select_own ON activity_logs")
    op.execute("DROP POLICY activity_logs_insert_own ON activity_logs")
    for table in OWNED_TABLES:
        op.execute(f"DROP POLICY {table}_owner ON {table}")
    for table in ALL_TABLES:
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {table} FROM anon, authenticated")
    op.execute("ALTER TABLE alembic_version DISABLE ROW LEVEL SECURITY")
    # Stand-in roles, the auth schema and auth.uid() stay: other objects or Supabase may use them.
