# Legacy Test Passport Policy

Pre-Phase-2 rows are test fixtures, not real physical devices. The migration preserves their original database primary keys and linked history, assigns a synthetic public ID for lookup/display, and sets `registration_status = LEGACY_TEST`.

That generated public ID is **not** an Algorand device key. The backend must not use it for live on-chain state reads or lifecycle transactions. The ledger-state endpoint returns `LEGACY_TEST_READ_ONLY`; existing off-chain transfer/dispute/event mirrors may still be viewed through the record mapping.

All mutation routes require `registration_status = CONFIRMED`. Legacy test rows are excluded from operational device counts and marketplace listings. They should not be deleted or manually marked `CONFIRMED` merely to make them appear active.

The SQL migration does not call Algorand and does not change deployed contracts. Back up PostgreSQL before applying the migration. If the earlier migration already ran, re-running the updated migration reclassifies rows marked `PHASE2_TEST_IDENTITY_RESET`; it cannot restore local transaction references that the earlier migration already cleared.
