-- Phase 4: bind each transfer completion to the verifier selected by the
-- current verification stamp. Existing in-progress transfers are backfilled
-- from the device's last recorded verifier; unresolvable rows remain unassigned
-- and must be re-verified before completion.

ALTER TABLE transfers
    ADD COLUMN IF NOT EXISTS assigned_verifier_id VARCHAR(36) NULL;

CREATE INDEX IF NOT EXISTS ix_transfers_assigned_verifier_id
    ON transfers (assigned_verifier_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_transfers_assigned_verifier_id_users'
    ) THEN
        ALTER TABLE transfers
            ADD CONSTRAINT fk_transfers_assigned_verifier_id_users
            FOREIGN KEY (assigned_verifier_id) REFERENCES users(id);
    END IF;
END $$;

UPDATE transfers AS t
SET assigned_verifier_id = d.last_verified_by
FROM devices AS d
WHERE t.device_id = d.id
  AND t.status = 'IN_PROGRESS'
  AND t.assigned_verifier_id IS NULL
  AND d.last_verified_by IS NOT NULL;
