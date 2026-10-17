-- TraceLoop Phase 2: V1 legacy identity segregation + V2 public-ID mapping.
-- Existing V1 rows are synthetic test passports: preserve their old PK and
-- history, attach a synthetic public lookup ID, and retain their original
-- on-chain key separately when it can be identified safely.
-- This migration does not submit transactions or modify the deployed contract.
-- Back up PostgreSQL before applying.

BEGIN;

ALTER TABLE public.devices
    ADD COLUMN IF NOT EXISTS public_id VARCHAR(40);
ALTER TABLE public.devices
    ADD COLUMN IF NOT EXISTS identity_version VARCHAR(20);
ALTER TABLE public.devices
    ADD COLUMN IF NOT EXISTS legacy_chain_id VARCHAR(120);

-- Validate only rows that need a generated public ID; don't silently create IDs
-- for an unsupported brand or overwrite already assigned public IDs.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM public.devices
        WHERE public_id IS NULL
          AND UPPER(COALESCE(brand_code, '')) NOT IN
              ('DL', 'DELL', 'HP', 'LN', 'LENOVO', 'AS', 'ASUS', 'AC', 'ACER', 'AP', 'APPLE', 'MS', 'MSI', 'SG', 'SAMSUNG')
    ) THEN
        RAISE EXCEPTION 'Cannot backfill public_id: devices contains an unsupported brand_code. Review those rows first.';
    END IF;
END
$$;

-- A row that predates public_id is a V1 record. Device.id is preserved because
-- child tables reference it. For V1, it is also the known original chain ID only
-- if it follows the historical TL- identifier shape; otherwise leave the chain
-- ID NULL rather than guess.
UPDATE public.devices
SET public_id =
        'TL-' ||
        CASE UPPER(brand_code)
            WHEN 'DL' THEN 'DELL'
            WHEN 'DELL' THEN 'DELL'
            WHEN 'HP' THEN 'HP'
            WHEN 'LN' THEN 'LENOVO'
            WHEN 'LENOVO' THEN 'LENOVO'
            WHEN 'AS' THEN 'ASUS'
            WHEN 'ASUS' THEN 'ASUS'
            WHEN 'AC' THEN 'ACER'
            WHEN 'ACER' THEN 'ACER'
            WHEN 'AP' THEN 'APPLE'
            WHEN 'APPLE' THEN 'APPLE'
            WHEN 'MS' THEN 'MSI'
            WHEN 'MSI' THEN 'MSI'
            WHEN 'SG' THEN 'SAMSUNG'
            WHEN 'SAMSUNG' THEN 'SAMSUNG'
        END || '-' || SUBSTRING(REPLACE(UPPER(gen_random_uuid()::TEXT), '-', '') FROM 1 FOR 24),
    identity_version = 'V1_LEGACY',
    legacy_chain_id = CASE WHEN id LIKE 'TL-%' THEN id ELSE NULL END,
    registration_status = 'LEGACY_TEST',
    registration_error_code = 'LEGACY_TEST_PUBLIC_ID_NOT_ON_CHAIN',
    is_for_sale = FALSE,
    stamp_valid = FALSE,
    registration_updated_at = CURRENT_TIMESTAMP
WHERE public_id IS NULL;

-- Repair records classified by the earlier migration revision. Preserve their
-- local transaction references and primary keys. Only recover a chain key from
-- Device.id when it has the known historic prefix.
UPDATE public.devices
SET identity_version = 'V1_LEGACY',
    legacy_chain_id = COALESCE(legacy_chain_id, CASE WHEN id LIKE 'TL-%' THEN id ELSE NULL END),
    registration_status = 'LEGACY_TEST',
    registration_error_code = 'LEGACY_TEST_PUBLIC_ID_NOT_ON_CHAIN',
    is_for_sale = FALSE,
    stamp_valid = FALSE,
    registration_updated_at = CURRENT_TIMESTAMP
WHERE identity_version = 'V1_LEGACY'
   OR registration_error_code IN ('PHASE2_TEST_IDENTITY_RESET', 'LEGACY_TEST_PUBLIC_ID_NOT_ON_CHAIN')
   OR registration_status = 'LEGACY_TEST';

-- Rows that already have a public ID and are not known legacy test rows belong
-- to the V2 identity model. This makes the migration re-runnable without
-- downgrading existing confirmed V2 registrations.
UPDATE public.devices
SET identity_version = 'V2_PUBLIC_ID'
WHERE identity_version IS NULL;

UPDATE public.devices
SET legacy_chain_id = COALESCE(legacy_chain_id, CASE WHEN id LIKE 'TL-%' THEN id ELSE NULL END)
WHERE identity_version = 'V1_LEGACY'
  AND legacy_chain_id IS NULL;

ALTER TABLE public.devices
    ALTER COLUMN public_id SET NOT NULL;
ALTER TABLE public.devices
    ALTER COLUMN identity_version SET DEFAULT 'V2_PUBLIC_ID';
ALTER TABLE public.devices
    ALTER COLUMN identity_version SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ix_devices_public_id
    ON public.devices (public_id);
CREATE INDEX IF NOT EXISTS ix_devices_identity_version
    ON public.devices (identity_version);

COMMIT;
