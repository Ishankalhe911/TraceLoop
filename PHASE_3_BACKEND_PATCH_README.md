# TraceLoop Phase 3 backend patch

This ZIP contains only the backend and deployment files changed or added for Phase 3, with paths relative to `trace-loop-clean/`.

## Included changes
- `pillar-2/routes/device_routes.py`: current user's approved/unregistered purchase-proof options endpoint.
- `pillar-2/routes/ownership_routes.py`: active recipient-role validation and consistent trimming of recipient ID.
- `pillar-2/routes/recycling_routes.py`: recycler pipeline query plus stricter state/ownership checks and durable no-blind-retry handling for uncertain chain attempts.
- `pillar-2/routes/user_routes.py`: OTP preview is explicitly opt-in and disabled by default.
- `pillar-2/database.py`: recovery fields for transfer and recycling ledger submissions.
- `pillar-2/migrations/20261019_phase3_transfer_attempt_tracking.sql` and `20261019_phase3_recycling_attempt_tracking.sql`: additive PostgreSQL schema migrations.
- `pillar-2/tests/test_phase3_backend.py`: Phase 3 regression tests using SQLite and a fake ledger.
- `pillar-2/app.py`: local React development origin is included in the default CORS list.
- `pillar-2/.env.example`: sample Gemini setting matching the intended Flash-Lite configuration and secure OTP preview default.
- `PHASE_3_DEPLOY_GUIDE.md`: migration and deployment notes.

## Required deployment step
Back up the Render database and apply both SQL migrations before deploying the new backend. `create_all()` will not add columns to existing production tables. Keep `OTP_PREVIEW_ENABLED=false` unless you explicitly need demo OTP disclosure in a controlled demo.

No smart-contract source or compiled contract artifact is included in this patch because none was changed.
