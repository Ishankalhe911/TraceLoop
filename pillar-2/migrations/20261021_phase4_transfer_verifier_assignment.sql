-- Phase 4: persist the verifier explicitly assigned to a transfer.
-- This migration is intentionally schema-only. Do NOT backfill from
-- devices.last_verified_by: it identifies the device's latest verifier, which
-- does not prove that verifier was selected for a historical transfer.
-- Existing unassigned IN_PROGRESS transfers must be explicitly resolved.

ALTER TABLE public.transfers
    ADD COLUMN IF NOT EXISTS assigned_verifier_id VARCHAR(36) NULL;

CREATE INDEX IF NOT EXISTS ix_transfers_assigned_verifier_id
    ON public.transfers (assigned_verifier_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_transfers_assigned_verifier_id_users'
          AND conrelid = 'public.transfers'::regclass
    ) THEN
        ALTER TABLE public.transfers
            ADD CONSTRAINT fk_transfers_assigned_verifier_id_users
            FOREIGN KEY (assigned_verifier_id) REFERENCES public.users(id);
    END IF;
END $$;
