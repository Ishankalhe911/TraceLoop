-- TraceLoop Phase 4: durable, user-scoped lifecycle notifications.
-- Safe to run if the table already exists: CREATE/INDEX statements are idempotent,
-- and RLS is enabled below even when the earlier version of this migration ran.
--
-- Security model:
--   * The browser must access notifications only through the authenticated FastAPI API.
--   * Do NOT add anon/authenticated client policies here. TraceLoop currently uses
--     its own application JWT/user IDs, not Supabase Auth's auth.uid() identity.
--   * The trusted FastAPI database connection must use a role that is permitted to
--     work with this table (normally its owner or a deliberately configured
--     backend role). Never use anon/authenticated credentials for DATABASE_URL.

BEGIN;

CREATE TABLE IF NOT EXISTS public.notifications (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES public.users(id),
    notification_type VARCHAR(48) NOT NULL,
    title VARCHAR(140) NOT NULL,
    message VARCHAR(600) NOT NULL,
    action_path VARCHAR(300),
    public_device_id VARCHAR(40),
    entity_id VARCHAR(36),
    dedupe_key VARCHAR(200) UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    read_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_notifications_user_id
    ON public.notifications(user_id);
CREATE INDEX IF NOT EXISTS ix_notifications_public_device_id
    ON public.notifications(public_device_id);
CREATE INDEX IF NOT EXISTS ix_notifications_created_at
    ON public.notifications(created_at);

-- Enable row-level security. With no policies, non-owner roles cannot read or
-- modify rows through PostgREST/anon/authenticated access (default-deny).
ALTER TABLE public.notifications ENABLE ROW LEVEL SECURITY;

-- Remove broad/default grants. PostgreSQL's PUBLIC pseudo-role always exists.
REVOKE ALL ON TABLE public.notifications FROM PUBLIC;

-- Supabase roles may not exist in a non-Supabase PostgreSQL deployment, so
-- revoke their privileges conditionally without breaking portability.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE 'REVOKE ALL ON TABLE public.notifications FROM anon';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        EXECUTE 'REVOKE ALL ON TABLE public.notifications FROM authenticated';
    END IF;
END
$$;

COMMENT ON TABLE public.notifications IS
    'Private TraceLoop lifecycle notifications; accessed via authenticated FastAPI endpoints.';

COMMIT;
