# TraceLoop frontend (React + Vite)

This is the active frontend entrypoint. The earlier multi-page HTML/JavaScript frontend is retained under `legacy-static-backup/` as a reference only and is not the Vite entrypoint.

## Local development

```bash
npm install
cp .env.example .env
npm run dev
```

`VITE_API_BASE` is the FastAPI backend origin. Existing token and profile localStorage keys are preserved so users can sign in through the existing backend; after switching deployments, test session refresh in a fresh browser profile too.

## Deployment

Deploy this directory as the Vercel project root with the Vite framework preset, `npm run build`, and output directory `dist`. Set `VITE_API_BASE` to the API origin and configure that frontend origin in the FastAPI `ALLOWED_ORIGINS` environment variable.

This frontend does not implement a public user or service-centre directory. IDs are explicitly exchanged in person. The owner must share a passport with the specific verifier before that verifier can view details or stamp it.

The passport explanation is still fetched from the same `/passport-intelligence/{public_id}` endpoint and uses the same reconciliation status, checks, timeline, source coverage, and limitations. The frontend changes only how this data is presented.
## Developer/demo OTP preview

The frontend never displays an OTP preview in the page and has no auto-fill action. When the backend response explicitly contains both `demo_otp_enabled: true` and a non-empty `otp_preview`, the sign-in flow logs the preview to the browser developer console (`F12` → Console) for manual entry during a controlled development/demo session.

The frontend cannot generate an OTP or recover one that the backend did not return. `OTP_PREVIEW_ENABLED` is off by default on the backend and should only be enabled in a controlled demo environment. **Moving an OTP from the page to the console is not a security boundary**: anyone who can request a preview can also inspect the network response. Keep previews disabled on a public/production service and use a real delivery channel (for example, an approved WhatsApp authentication template or SMS) for real users.

If no OTP appears in the console, inspect the `/api/v1/otp/send` response. If `demo_otp_enabled` is `false` or `otp_preview` is `null`, the frontend will not invent or display an OTP.



## Public sample passport (optional)

The landing page includes **Explore a sample passport**. Configure `VITE_DEMO_PASSPORT_ID` in the Vercel project environment with an actual, publicly viewable, non-sensitive Passport ID from the deployed backend, then redeploy the frontend. The sample link automatically opens that passport and runs the reconciliation explanation. Do not put private ownership data, raw serial numbers, or a fabricated ID in this variable. If the variable is empty, the UI explains that a sample has not been configured instead of inventing a device.

## Lifecycle notifications

The authenticated notification centre reads `GET /api/v1/notifications`, marks individual/all updates as read, refreshes on focus and while the tab is visible, and polls at a 30-second interval. The backend writes a notification in the same database transaction as supported lifecycle transitions, so new updates remain available after the user signs out or returns later. Existing ledger history is not backfilled as unread notifications.

Before deploying the backend version with notifications to an existing PostgreSQL database, apply `pillar-2/migrations/20261019_phase4_notifications.sql` (or verify the `notifications` table already exists). The frontend change alone cannot create backend notifications.

## Frontend environment

- `VITE_API_BASE`: deployed FastAPI base URL.
- `VITE_DEMO_PASSPORT_ID`: optional real public passport ID for the sample demo CTA.

Keep `.env.local` and Vercel credentials out of shared source archives. Use Vercel's environment-variable settings for deployment secrets and demo configuration.

## State-based next-step guidance

Workspace, verifier, recycler, and public-passport screens use the current returned state to show the next technical step. Where a step can stall or fail, the guidance explains the safe recovery path (refresh/recheck/reconcile before a possibly duplicate irreversible action). This is workflow guidance only; it does not add chat or contact functionality.
