import hashlib
import hmac
import logging
import os
import re
import uuid
from datetime import datetime, time as datetime_time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
import requests
from urllib.parse import quote
from services.notification_service import create_notification

from auth import JWT_SECRET, decode_token, get_current_user_any_status
from database import (
    AdminAction,
    AdminActionType,
    DocStatus,
    DocType,
    EvidenceRecord,
    EvidenceSubjectType,
    EvidenceType,
    EvidenceVerificationStatus,
    KYCDocument,
    User,
    UserRole,
    UserStatus,
    get_db,
)

logger = logging.getLogger("TraceLoop.EvidenceRoutes")
router = APIRouter(tags=["Evidence & Protected Viewer"])

if JWT_SECRET == "traceloop-dev-secret-change-in-prod":
    logger.warning(
        "SECURITY WARNING: JWT_SECRET is using the development fallback. "
        "Configure a strong random JWT_SECRET in production."
    )

# Supabase Storage configuration. DATABASE_URL is only for PostgreSQL; these settings
# are separately required for private object storage.
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
# Accept the current Supabase secret-key variable name and the older environment name.
# A modern sb_secret_... key is not a JWT and must NOT be sent as a Bearer token.
SUPABASE_SERVICE_ROLE_KEY = (
    os.getenv("SUPABASE_SECRET_KEY")
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or ""
).strip()
SUPABASE_EVIDENCE_BUCKET = os.getenv(
    "SUPABASE_EVIDENCE_BUCKET", "traceloop-evidence-private"
).strip()
SUPABASE_REQUEST_TIMEOUT = (8, 40)  # connect timeout, read timeout

MAX_EVIDENCE_BYTES = 5 * 1024 * 1024  # 5 MB hard cap
CHUNK_SIZE_BYTES = 64 * 1024          # 64 KB streaming chunks
CERTIFICATE_TIMEZONE = ZoneInfo("Asia/Kolkata")

# Phase 1 deliberately excludes RECYCLING_RECOVERY_PROOF until it can be scoped to an
# active recycler and a matching, valid recycling workflow/device record.
ROLE_ALLOWED_EVIDENCE = {
    UserRole.FIRST_BUYER: {EvidenceType.PURCHASE_PROOF},
    UserRole.BUYER: {EvidenceType.IDENTITY_PROOF},
    UserRole.RESELLER: {EvidenceType.BUSINESS_REGISTRATION},
    UserRole.VERIFIABLE: {EvidenceType.BRAND_AUTHORIZATION},
    UserRole.RECYCLER: {EvidenceType.RECYCLER_AUTHORIZATION},
}

ROLE_CREDENTIAL_FOR_ROLE = {
    UserRole.VERIFIABLE: EvidenceType.BRAND_AUTHORIZATION,
    UserRole.RECYCLER: EvidenceType.RECYCLER_AUTHORIZATION,
    UserRole.RESELLER: EvidenceType.BUSINESS_REGISTRATION,
}

EVIDENCE_TO_LEGACY_DOCTYPE = {
    EvidenceType.PURCHASE_PROOF: DocType.PURCHASE_PROOF,
    EvidenceType.BUSINESS_REGISTRATION: DocType.BUSINESS_PROOF,
    EvidenceType.BRAND_AUTHORIZATION: DocType.BRAND_AUTH,
    EvidenceType.RECYCLER_AUTHORIZATION: DocType.CPCB_CERT,
    EvidenceType.IDENTITY_PROOF: None,
}

# Phase 1 time-bound enforcement is intentionally disabled for now so registration,
# evidence upload, admin review and account activation can be aligned and tested first.
# Reintroduce the policy only after the CPCB/brand-certificate expiry rules and UI are agreed.
TIME_BOUND_ENFORCEMENT_ENABLED = False
TIME_BOUND_CREDENTIALS: set[EvidenceType] = set()
# Future policy (do not enable until the expiry workflow is implemented end-to-end):
# TIME_BOUND_CREDENTIALS = {EvidenceType.RECYCLER_AUTHORIZATION}


def standard_response(data: dict = None, code: int = 200) -> dict:
    return {
        "status": "success",
        "code": code,
        "data": data or {},
        "error": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "traceloop_version": "v1",
    }


def normalize_serial(raw_serial: Optional[str]) -> Optional[str]:
    """Canonical evidence-side serial normalizer. Device routes must use the same rule."""
    if not raw_serial or not raw_serial.strip():
        return None
    cleaned = re.sub(r"[^A-Z0-9-]", "", raw_serial.strip().upper())
    return cleaned or None


def sanitize_filename(raw_filename: Optional[str], fallback: str) -> str:
    """Remove path parts, control characters and header-sensitive punctuation."""
    base = os.path.basename((raw_filename or "").strip())
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._")
    return cleaned[:200] if cleaned else fallback


def _make_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalize a SQLAlchemy datetime to aware UTC; SQLite may return naive values."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        # All datetimes written by this module are normalized to UTC before persistence.
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_certificate_date(raw_str: Optional[str], end_of_day: bool = False) -> Optional[datetime]:
    """Parse a certificate date/time and return UTC.

    Date-only values use Asia/Kolkata. An expiry date is valid through the end of that
    calendar day in India. A datetime with an explicit offset is interpreted at that
    instant. A datetime with no offset is treated as local India time.
    """
    if not raw_str or not raw_str.strip():
        return None

    value = raw_str.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            day = datetime.fromisoformat(value).date()
            if end_of_day:
                local_dt = datetime.combine(day, datetime_time(23, 59, 59, 999999), tzinfo=CERTIFICATE_TIMEZONE)
            else:
                local_dt = datetime.combine(day, datetime_time.min, tzinfo=CERTIFICATE_TIMEZONE)
            return local_dt.astimezone(timezone.utc)

        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CERTIFICATE_TIMEZONE)
        return dt.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dates must be valid ISO-8601 values (YYYY-MM-DD or a valid ISO-8601 datetime).",
        )


def certificate_date_for_display(dt: Optional[datetime]) -> Optional[str]:
    """Return the certificate calendar date in India for user/admin display."""
    value = _make_utc(dt)
    if value is None:
        return None
    return value.astimezone(CERTIFICATE_TIMEZONE).date().isoformat()


def _detect_file_signature(header_bytes: bytes) -> Optional[tuple[str, str]]:
    if header_bytes.startswith(b"%PDF-"):
        return ("application/pdf", ".pdf")
    if header_bytes.startswith(b"\xff\xd8\xff"):
        return ("image/jpeg", ".jpg")
    if header_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return ("image/png", ".png")
    if len(header_bytes) >= 12 and header_bytes[:4] == b"RIFF" and header_bytes[8:12] == b"WEBP":
        return ("image/webp", ".webp")
    return None


def _resolve_evidence_type(raw_doc_type: str, user_role: UserRole) -> EvidenceType:
    cleaned = (raw_doc_type or "").strip().upper()
    # Backward compatibility with the older UI, which used BUSINESS_PROOF for buyer identity.
    if cleaned == "BUSINESS_PROOF":
        return EvidenceType.IDENTITY_PROOF if user_role == UserRole.BUYER else EvidenceType.BUSINESS_REGISTRATION

    mapping = {
        "PURCHASE_PROOF": EvidenceType.PURCHASE_PROOF,
        "IDENTITY_PROOF": EvidenceType.IDENTITY_PROOF,
        "BUSINESS_REGISTRATION": EvidenceType.BUSINESS_REGISTRATION,
        "BRAND_AUTH": EvidenceType.BRAND_AUTHORIZATION,
        "BRAND_AUTHORIZATION": EvidenceType.BRAND_AUTHORIZATION,
        "CPCB_CERT": EvidenceType.RECYCLER_AUTHORIZATION,
        "RECYCLER_AUTHORIZATION": EvidenceType.RECYCLER_AUTHORIZATION,
    }
    if cleaned not in mapping:
        raise HTTPException(status_code=400, detail=f"Unsupported document type: {raw_doc_type}")
    return mapping[cleaned]



def _append_review_note(existing: Optional[str], note: str) -> str:
    """Append a review/audit note without duplicating the same message."""
    if not existing:
        return note
    if note in existing:
        return existing
    return f"{existing}\n{note}"[:4000]


def sync_expired_evidence_for_user(user: User, db: Session) -> bool:
    """Apply the future expiry policy only when an approved credential is known to expire.

    A missing EvidenceRecord is not treated as proof of expiry: it may be a legacy
    data-mapping issue and must be reviewed rather than silently changing account
    status on profile/list reads. Time-bound enforcement remains disabled for Phase 4.
    """
    if not TIME_BOUND_ENFORCEMENT_ENABLED:
        return False

    required_type = ROLE_CREDENTIAL_FOR_ROLE.get(user.role)
    if required_type is None:
        return False

    now_utc = datetime.now(timezone.utc)
    verified_records = (
        db.query(EvidenceRecord)
        .filter(
            EvidenceRecord.user_id == user.id,
            EvidenceRecord.evidence_type == required_type,
            EvidenceRecord.subject_type == EvidenceSubjectType.USER_ROLE_CREDENTIAL,
            EvidenceRecord.verification_status == EvidenceVerificationStatus.VERIFIED,
        )
        .all()
    )
    # No credential row is a credential-review issue, not an expiry event. Active
    # status is changed for the specific case where a previously verified credential
    # was present and the configured expiry policy has now invalidated it.
    had_verified_credential = bool(verified_records)
    changed = False
    has_valid_verified = False
    for rec in verified_records:
        exp_utc = _make_utc(rec.expires_at)
        invalid_for_missing_expiry = required_type in TIME_BOUND_CREDENTIALS and exp_utc is None
        invalid_for_past_expiry = exp_utc is not None and exp_utc <= now_utc

        if invalid_for_missing_expiry or invalid_for_past_expiry:
            rec.verification_status = EvidenceVerificationStatus.EXPIRED
            reason = (
                "System expiry check: required expiry date was missing."
                if invalid_for_missing_expiry
                else f"System expiry check: credential expired at {exp_utc.isoformat()}."
            )
            rec.review_notes = _append_review_note(rec.review_notes, reason)
            changed = True
        else:
            has_valid_verified = True

    transitioned_to_kyc = (
        had_verified_credential
        and not has_valid_verified
        and user.status == UserStatus.ACTIVE
    )
    if transitioned_to_kyc:
        user.status = UserStatus.KYC_IN_PROGRESS
        user.admin_approved = False
        create_notification(
            db,
            user_id=user.id,
            notification_type="credential_expired",
            title="Role credential needs renewal",
            message=(
                f"Your approved {required_type.value} credential is no longer valid under the configured expiry policy. "
                "Protected role actions are paused. Upload a renewed document in onboarding and wait for review."
            ),
            action_path="/onboarding",
            entity_id=next((rec.id for rec in verified_records if rec.verification_status == EvidenceVerificationStatus.EXPIRED), None),
            dedupe_key=f"credential-expiry:{user.id}:{required_type.value}:{now_utc.date().isoformat()}",
        )
        changed = True

    if changed:
        db.commit()
    return changed

def assert_active_role_evidence(
    user: User,
    required_type: EvidenceType,
    db: Session,
    *,
    transition_account_on_failure: bool = False,
) -> EvidenceRecord:
    """Return a valid verified credential, or block the protected operation.

    A valid older credential remains usable while a renewal is pending/rejected, until
    that credential expires or is explicitly revoked. A newer VERIFIED renewal takes
    effect after its review. A revocation made after the candidate credential was
    verified invalidates that older candidate.
    """
    # Do not synchronize expiry or mutate account state as a side effect of a listing
    # or credential lookup. The operational caller may explicitly opt into KYC
    # transition after this check establishes that no valid credential exists.
    if transition_account_on_failure:
        sync_expired_evidence_for_user(user, db)

    records = (
        db.query(EvidenceRecord)
        .filter(
            EvidenceRecord.user_id == user.id,
            EvidenceRecord.evidence_type == required_type,
            EvidenceRecord.subject_type == EvidenceSubjectType.USER_ROLE_CREDENTIAL,
        )
        .order_by(EvidenceRecord.created_at.desc(), EvidenceRecord.id.desc())
        .all()
    )
    if not records:
        detail = f"No uploaded {required_type.value} evidence record found for your account."
        if transition_account_on_failure:
            _move_active_role_account_to_kyc(user, required_type, db, detail)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)

    now_utc = datetime.now(timezone.utc)
    revocation_times = [
        _make_utc(rec.reviewed_at)
        for rec in records
        if rec.verification_status == EvidenceVerificationStatus.REVOKED and rec.reviewed_at is not None
    ]
    latest_revocation_at = max(revocation_times) if revocation_times else None

    valid_verified: list[EvidenceRecord] = []
    for rec in records:
        if rec.verification_status != EvidenceVerificationStatus.VERIFIED:
            continue

        exp_utc = _make_utc(rec.expires_at)
        if TIME_BOUND_ENFORCEMENT_ENABLED:
            if required_type in TIME_BOUND_CREDENTIALS and exp_utc is None:
                # Normally already transitioned by sync_expired_evidence_for_user; fail closed.
                continue
            if exp_utc is not None and exp_utc <= now_utc:
                continue

        verified_at = _make_utc(rec.reviewed_at) or _make_utc(rec.created_at)
        if latest_revocation_at and verified_at and verified_at <= latest_revocation_at:
            continue
        valid_verified.append(rec)

    if valid_verified:
        # Prefer the most recently reviewed credential, not merely the newest upload.
        credential = max(
            valid_verified,
            key=lambda rec: _make_utc(rec.reviewed_at) or _make_utc(rec.created_at) or datetime.min.replace(tzinfo=timezone.utc),
        )
        if transition_account_on_failure:
            # A protected operation checks the stored evidence itself. A confirmed
            # missing object or hash/size mismatch is a genuine credential-integrity
            # failure and pauses the account for review. Storage/network errors only
            # block this operation; they must never mutate account approval state.
            try:
                verify_evidence_file_integrity(credential)
            except HTTPException as exc:
                if exc.status_code in {404, 409}:
                    detail = (
                        f"The approved {required_type.value} document is missing or failed its integrity check. "
                        "The protected operation was blocked and the account was moved to KYC review. "
                        "Upload corrected evidence and wait for administrator review."
                    )
                    _move_active_role_account_to_kyc(user, required_type, db, detail)
                    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail) from None
                # For example, HTTP 502 indicates storage could not be reached. Keep
                # the account status untouched so a temporary outage cannot revoke trust.
                raise
        return credential

    latest = records[0]
    if latest.verification_status == EvidenceVerificationStatus.PENDING:
        detail = (
            f"Your {required_type.value} credential is PENDING administrator verification. "
            "Operations remain blocked until a valid credential is approved."
        )
    elif latest.verification_status == EvidenceVerificationStatus.EXPIRED:
        detail = f"Your {required_type.value} certificate has expired. Upload a renewed certificate for re-verification."
    elif latest.verification_status == EvidenceVerificationStatus.REVOKED:
        detail = f"Your {required_type.value} credential has been revoked by an administrator."
    elif latest.verification_status == EvidenceVerificationStatus.REJECTED:
        detail = f"Your latest {required_type.value} submission was rejected. Upload corrected evidence for review."
    else:
        detail = f"No currently valid VERIFIED {required_type.value} credential is available. Re-verification is required."

    if transition_account_on_failure:
        _move_active_role_account_to_kyc(user, required_type, db, detail)

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _move_active_role_account_to_kyc(
    user: User,
    required_type: EvidenceType,
    db: Session,
    reason: str,
) -> None:
    """Pause an active role account only after an operational check proves a credential gap."""
    if user.status != UserStatus.ACTIVE or user.role not in ROLE_CREDENTIAL_FOR_ROLE:
        return
    user.status = UserStatus.KYC_IN_PROGRESS
    user.admin_approved = False
    create_notification(
        db,
        user_id=user.id,
        notification_type="credential_review_required",
        title="Role credential review required",
        message=(
            f"TraceLoop could not find a currently valid {required_type.value} credential for a protected operation. "
            f"That operation was blocked and your account was moved to KYC review. Details: {reason} "
            "Upload corrected or renewed evidence in onboarding; protected actions remain unavailable until review and approval."
        ),
        action_path="/onboarding",
        dedupe_key=f"credential-gap:{user.id}:{required_type.value}:{datetime.now(timezone.utc).isoformat()}",
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Could not persist KYC transition for user %s after credential failure", user.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The protected action was blocked, but the account review state could not be saved. Retry later.",
        )


def _check_existing_role_credential_before_replacement_upload(
    user: User, evidence_type: EvidenceType, db: Session
) -> None:
    """Force re-review when an ACTIVE verifier/recycler's approved file is confirmed bad.

    An ordinary renewal does not suspend an active account while its approved evidence
    remains intact. If a role-credential replacement is submitted and the prior approved
    credential is missing/tampered, transition the account to KYC before accepting the
    new row. That prevents upload+review alone from bypassing the separate account
    approval step after a confirmed credential-integrity failure. Transient storage
    errors do not demote the account; protected operations still fail closed on retry.
    """
    if user.status != UserStatus.ACTIVE or user.role not in {UserRole.VERIFIABLE, UserRole.RECYCLER}:
        return
    required_type = ROLE_CREDENTIAL_FOR_ROLE.get(user.role)
    if required_type is None or evidence_type != required_type:
        return

    try:
        credential = assert_active_role_evidence(user, required_type, db)
    except HTTPException as exc:
        _move_active_role_account_to_kyc(
            user,
            required_type,
            db,
            f"A replacement credential was submitted, but no currently valid approved credential was available: {exc.detail}",
        )
        return

    try:
        verify_evidence_file_integrity(credential)
    except HTTPException as exc:
        if exc.status_code in {404, 409}:
            _move_active_role_account_to_kyc(
                user,
                required_type,
                db,
                f"A replacement credential was submitted and the previously approved file is confirmed missing or tampered: {exc.detail}",
            )
        else:
            # Do not turn an infrastructure/configuration outage into revocation. The new
            # evidence can still be uploaded, but each protected operation will independently
            # verify the credential/file and fail closed if its check cannot complete.
            logger.warning(
                "Could not check existing credential for user %s before replacement upload: %s",
                user.id,
                exc.detail,
            )


def _storage_configuration() -> tuple[str, str, str]:
    """Return configured Supabase Storage settings without exposing secret values."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Supabase Storage is not configured. Set SUPABASE_URL and either "
                "SUPABASE_SECRET_KEY or SUPABASE_SERVICE_ROLE_KEY on the backend."
            ),
        )
    if not SUPABASE_URL.startswith("https://"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SUPABASE_URL must be the HTTPS Project URL from the Supabase dashboard.",
        )
    if not SUPABASE_EVIDENCE_BUCKET or "/" in SUPABASE_EVIDENCE_BUCKET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SUPABASE_EVIDENCE_BUCKET must be a valid bucket name.",
        )
    return SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_EVIDENCE_BUCKET


def _storage_headers(content_type: Optional[str] = None) -> dict[str, str]:
    _, key, _ = _storage_configuration()
    headers = {
        "apikey": key,
        "Cache-Control": "no-store",
    }
    # Legacy service_role keys are JWTs. Modern sb_secret_ keys are opaque API keys;
    # sending one as Authorization: Bearer causes JWT validation errors.
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _storage_object_url(file_reference: str) -> str:
    """Build an object URL for a validated key inside our configured private bucket."""
    base_url, _, bucket = _storage_configuration()
    key = (file_reference or "").strip().replace("\\", "/")
    parts = key.split("/")
    if (
        not key
        or key.startswith("/")
        or any(part in {"", ".", ".."} for part in parts)
        or "\x00" in key
    ):
        raise HTTPException(status_code=404, detail="Private evidence storage reference is invalid.")
    encoded_bucket = quote(bucket, safe="")
    encoded_key = quote(key, safe="/")
    return f"{base_url}/storage/v1/object/{encoded_bucket}/{encoded_key}"


def upload_private_evidence_object(file_reference: str, content: bytes, mime_type: str) -> None:
    """Upload bytes to a private Supabase Storage bucket; never create public URLs."""
    url = _storage_object_url(file_reference)
    headers = _storage_headers(mime_type)
    headers["x-upsert"] = "false"
    headers["cache-control"] = "max-age=0"
    try:
        response = requests.post(
            url,
            headers=headers,
            data=content,
            timeout=SUPABASE_REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.exception("Supabase Storage upload request failed")
        raise RuntimeError("Supabase Storage upload request failed.") from exc
    if response.status_code not in (200, 201):
        logger.error(
            "Supabase Storage upload failed: status=%s body=%s",
            response.status_code,
            (response.text or "")[:300],
        )
        raise RuntimeError(f"Supabase Storage upload failed with HTTP {response.status_code}.")


def download_private_evidence_object(file_reference: str) -> bytes:
    """Download an object through the backend service credential, never a public URL."""
    url = _storage_object_url(file_reference)
    try:
        response = requests.get(
            url,
            headers=_storage_headers(),
            timeout=SUPABASE_REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.exception("Supabase Storage download request failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not retrieve evidence from private storage. Try again later.",
        ) from exc
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="Private evidence file is missing from Supabase Storage.")
    if response.status_code != 200:
        logger.error("Supabase Storage download failed: status=%s", response.status_code)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Private evidence storage returned an error. The file cannot be reviewed.",
        )
    content = response.content
    if len(content) > MAX_EVIDENCE_BYTES:
        logger.error("Stored evidence exceeded configured max size for reference %s", file_reference)
        raise HTTPException(status_code=409, detail="Stored evidence exceeds the allowed size limit.")
    return content


def delete_private_evidence_object(file_reference: str) -> None:
    """Best-effort cleanup for an uploaded object whose database transaction failed."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return
    try:
        response = requests.delete(
            _storage_object_url(file_reference),
            headers=_storage_headers(),
            timeout=SUPABASE_REQUEST_TIMEOUT,
        )
        if response.status_code not in (200, 204, 404):
            logger.error(
                "Supabase Storage cleanup failed: status=%s reference=%s",
                response.status_code,
                file_reference,
            )
    except Exception:
        logger.exception("Supabase Storage cleanup request failed for %s", file_reference)


def evidence_object_exists(file_reference: str) -> bool:
    """Check existence without downloading the object; used by the admin review queue."""
    url = _storage_object_url(file_reference)
    try:
        response = requests.head(
            url,
            headers=_storage_headers(),
            timeout=SUPABASE_REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.exception("Supabase Storage existence check failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not check the evidence object in private storage.",
        ) from exc
    if response.status_code == 404:
        return False
    if response.status_code == 200:
        return True
    logger.error("Supabase Storage existence check failed: status=%s", response.status_code)
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="Private storage could not confirm whether the evidence file exists.",
    )


def evidence_file_presence_status(evidence: Optional[EvidenceRecord]) -> tuple[str, Optional[str]]:
    """Read-only report of object existence for admin lists; never changes account state."""
    if evidence is None:
        return "NOT_CHECKED_NO_VALID_CREDENTIAL", None
    try:
        exists = evidence_object_exists(evidence.file_reference)
    except HTTPException as exc:
        detail = str(exc.detail)
        if exc.status_code == 404 and "reference is invalid" in detail.lower():
            return "INVALID_REFERENCE", detail
        if exc.status_code >= 500:
            return "STORAGE_UNAVAILABLE", detail
        return "CHECK_FAILED", detail
    if not exists:
        return "MISSING", "The approved evidence file is missing from private storage."
    return "PRESENT", None


def verify_evidence_file_integrity(evidence: EvidenceRecord) -> bytes:
    """Download and hash the private object, returning verified bytes or raising an error."""
    content = download_private_evidence_object(evidence.file_reference)
    actual = hashlib.sha256(content).hexdigest()
    expected = (evidence.file_sha256 or "").lower()
    if not expected or not hmac.compare_digest(actual, expected):
        logger.error("Evidence integrity mismatch for evidence %s", evidence.id)
        raise HTTPException(
            status_code=409,
            detail="Stored evidence failed its integrity check. The file cannot be accepted or served for review.",
        )
    if evidence.file_size_bytes is not None and len(content) != evidence.file_size_bytes:
        logger.error("Evidence size mismatch for evidence %s", evidence.id)
        raise HTTPException(
            status_code=409,
            detail="Stored evidence size does not match its metadata. The file cannot be accepted or served for review.",
        )
    return content


@router.post("/api/v1/evidence/upload")
async def upload_evidence_file(
    doc_type: str = Form(..., description="PURCHASE_PROOF, IDENTITY_PROOF, BUSINESS_PROOF, BRAND_AUTH, CPCB_CERT, or EvidenceType"),
    serial_for_device: Optional[str] = Form(None),
    claimed_document_number: Optional[str] = Form(None),
    claimed_issuer: Optional[str] = Form(None),
    issued_at: Optional[str] = Form(None),
    expires_at: Optional[str] = Form(None),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user_any_status),
    db: Session = Depends(get_db),
):
    # Run expiry synchronization before staging any new rows; the helper may commit.
    sync_expired_evidence_for_user(current_user, db)

    # REJECTED users may submit corrected evidence to reopen onboarding, but cannot
    # regain protected access by uploading. SUSPENDED accounts require explicit admin action.
    if current_user.status not in {UserStatus.KYC_IN_PROGRESS, UserStatus.ACTIVE, UserStatus.REJECTED}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Account in status {current_user.status.value} cannot upload evidence documents.",
        )

    ev_type = _resolve_evidence_type(doc_type, current_user.role)
    allowed_for_role = ROLE_ALLOWED_EVIDENCE.get(current_user.role, set())
    if ev_type not in allowed_for_role:
        allowed_names = sorted(item.value for item in allowed_for_role)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Role {current_user.role.value} cannot upload {ev_type.value}. Allowed: {allowed_names}",
        )

    # If an already-active verifier/recycler is replacing role evidence, confirm the old
    # approved evidence remains available. A confirmed gap requires admin re-approval; a
    # temporary storage outage alone must not downgrade the account.
    _check_existing_role_credential_before_replacement_upload(current_user, ev_type, db)

    clean_serial = normalize_serial(serial_for_device)
    if ev_type == EvidenceType.PURCHASE_PROOF:
        if not clean_serial:
            raise HTTPException(status_code=400, detail="serial_for_device is required for PURCHASE_PROOF.")
        if len(clean_serial) > 80:
            raise HTTPException(status_code=400, detail="Normalized serial number exceeds the 80-character limit.")
        subject_type = EvidenceSubjectType.DEVICE_CLAIM
        subject_id = clean_serial
    else:
        subject_type = EvidenceSubjectType.USER_ROLE_CREDENTIAL
        subject_id = current_user.id

    parsed_issued_at = parse_certificate_date(issued_at, end_of_day=False)
    claimed_expiry = parse_certificate_date(expires_at, end_of_day=True)
    now_utc = datetime.now(timezone.utc)

    if TIME_BOUND_ENFORCEMENT_ENABLED and ev_type in TIME_BOUND_CREDENTIALS and claimed_expiry is None:
        raise HTTPException(
            status_code=400,
            detail=f"expires_at (the date printed on the certificate) is required when uploading {ev_type.value}.",
        )
    if TIME_BOUND_ENFORCEMENT_ENABLED and claimed_expiry is not None and claimed_expiry <= now_utc:
        raise HTTPException(status_code=400, detail="The uploaded certificate expiry date must be in the future.")
    if parsed_issued_at is not None and parsed_issued_at > now_utc:
        raise HTTPException(status_code=400, detail="issued_at cannot be in the future.")
    if TIME_BOUND_ENFORCEMENT_ENABLED and parsed_issued_at is not None and claimed_expiry is not None and claimed_expiry <= parsed_issued_at:
        raise HTTPException(status_code=400, detail="expires_at must be later than issued_at.")

    clean_doc_num = claimed_document_number.strip() if claimed_document_number and claimed_document_number.strip() else None
    clean_issuer = claimed_issuer.strip() if claimed_issuer and claimed_issuer.strip() else None
    if clean_doc_num and len(clean_doc_num) > 80:
        raise HTTPException(status_code=400, detail="claimed_document_number must be at most 80 characters.")
    if clean_issuer and len(clean_issuer) > 120:
        raise HTTPException(status_code=400, detail="claimed_issuer must be at most 120 characters.")

    if not clean_doc_num:
        if ev_type == EvidenceType.RECYCLER_AUTHORIZATION:
            clean_doc_num = current_user.cpcb_number
        elif ev_type == EvidenceType.BUSINESS_REGISTRATION:
            clean_doc_num = current_user.gst_number
        elif ev_type == EvidenceType.BRAND_AUTHORIZATION:
            clean_doc_num = current_user.brand_auth_code

    hasher = hashlib.sha256()
    total_bytes = 0
    chunks: list[bytes] = []
    try:
        while True:
            chunk = await file.read(CHUNK_SIZE_BYTES)
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > MAX_EVIDENCE_BYTES:
                raise HTTPException(status_code=413, detail="Uploaded file exceeds the 5 MB maximum size limit.")
            hasher.update(chunk)
            chunks.append(chunk)
    finally:
        await file.close()

    if total_bytes == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    raw_bytes = b"".join(chunks)
    detected = _detect_file_signature(raw_bytes[:16])
    if not detected:
        raise HTTPException(
            status_code=400,
            detail="Invalid file signature. Only PDF, JPEG, PNG, or WebP files are accepted.",
        )

    detected_mime, extension = detected
    computed_sha256 = hasher.hexdigest()
    evidence_id = f"EV-{uuid.uuid4().hex.upper()}"  # 35 chars; fits EvidenceRecord.id String(36).
    # Keep the object key opaque and scoped by user; the bucket itself must be private.
    stored_filename = f"users/{current_user.id}/{evidence_id}{extension}"
    safe_original_name = sanitize_filename(file.filename, os.path.basename(stored_filename))
    storage_uploaded = False
    db_committed = False

    # Expiry is currently optional metadata only. The later time-bound feature must align
    # upload validation, admin confirmation, authorization enforcement and frontend controls.
    try:
        await run_in_threadpool(upload_private_evidence_object, stored_filename, raw_bytes, detected_mime)
        storage_uploaded = True

        legacy_doc_id = None
        legacy_doctype = EVIDENCE_TO_LEGACY_DOCTYPE.get(ev_type)
        if legacy_doctype is not None:
            legacy_doc = KYCDocument(
                user_id=current_user.id,
                doc_type=legacy_doctype,
                file_path=f"supabase://{SUPABASE_EVIDENCE_BUCKET}/{stored_filename}",
                file_hash=computed_sha256,
                serial_for_device=subject_id if subject_type == EvidenceSubjectType.DEVICE_CLAIM else None,
                status=DocStatus.PENDING,
            )
            db.add(legacy_doc)
            db.flush()
            legacy_doc_id = legacy_doc.id

        ev_record = EvidenceRecord(
            id=evidence_id,
            user_id=current_user.id,
            evidence_type=ev_type,
            subject_type=subject_type,
            subject_id=subject_id,
            claimed_document_number=clean_doc_num,
            claimed_issuer=clean_issuer,
            issued_at=parsed_issued_at,
            # This value is a claim until admin acceptance; status=PENDING prevents it from authorizing.
            expires_at=claimed_expiry,
            file_reference=stored_filename,
            original_filename=safe_original_name,
            mime_type=detected_mime,
            file_size_bytes=total_bytes,
            file_sha256=computed_sha256,
            verification_status=EvidenceVerificationStatus.PENDING,
            legacy_kyc_doc_id=legacy_doc_id,
        )
        db.add(ev_record)
        # An explicit corrected-evidence submission is the user's request to reopen a
        # REJECTED account's KYC flow. It does not activate them; admin review is still required.
        was_rejected = current_user.status == UserStatus.REJECTED
        if was_rejected:
            current_user.status = UserStatus.KYC_IN_PROGRESS
            current_user.admin_approved = False

        if was_rejected:
            submission_message = (
                f"Your corrected {ev_type.value} evidence was received. Your account has returned to KYC_IN_PROGRESS; "
                "protected actions remain locked until the evidence is reviewed and the required approval is completed."
            )
        elif current_user.status == UserStatus.ACTIVE:
            submission_message = (
                f"Your {ev_type.value} evidence was submitted for review. Your existing ACTIVE account status is unchanged "
                "while this document is reviewed; only an explicit admin action or a confirmed credential-validity failure changes access."
            )
        else:
            submission_message = f"Your {ev_type.value} evidence was submitted for admin review. Check onboarding for the decision and next step."

        create_notification(
            db,
            user_id=current_user.id,
            notification_type="evidence_submitted",
            title="Evidence submitted for review",
            message=submission_message,
            action_path="/onboarding",
            entity_id=ev_record.id,
            dedupe_key=f"evidence:{ev_record.id}:submitted:{current_user.id}",
        )
        db.commit()
        db_committed = True
        db.refresh(ev_record)
        db.refresh(current_user)

        return standard_response(
            {
                "evidence_id": ev_record.id,
                "doc_id": legacy_doc_id or ev_record.id,
                "doc_type": ev_type.value,
                "subject_type": subject_type.value,
                "serial_for_device": subject_id if subject_type == EvidenceSubjectType.DEVICE_CLAIM else None,
                "expires_at_claimed": certificate_date_for_display(ev_record.expires_at),
                "file_sha256": computed_sha256,
                "status": ev_record.verification_status.value,
                "user_status": current_user.status.value,
                "message": (
                    "Corrected evidence submitted; your account is back in KYC review and is not yet active."
                    if was_rejected
                    else "Document uploaded privately. Any supplied expiry date remains unverified until admin review."
                ),
            },
            201,
        )
    except HTTPException:
        db.rollback()
        if storage_uploaded and not db_committed:
            await run_in_threadpool(delete_private_evidence_object, stored_filename)
        raise
    except Exception:
        db.rollback()
        if storage_uploaded and not db_committed:
            await run_in_threadpool(delete_private_evidence_object, stored_filename)
        logger.exception("Evidence upload failed for user %s", current_user.id)
        raise HTTPException(status_code=500, detail="Evidence upload failed due to an internal server error.")


@router.get("/admin/evidence/{evidence_id}/view")
def view_protected_evidence_file(
    evidence_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer()),
    db: Session = Depends(get_db),
):
    """Serve private evidence only to an active admin authenticated with a Bearer access JWT.

    No signed URL or query-string bearer token is supported. A copied URL alone must not
    grant access to an evidence document.
    """
    try:
        payload = decode_token(credentials.credentials)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired access token.")
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type.")
    admin_user_id = payload.get("sub")

    admin_user = db.query(User).filter(User.id == admin_user_id).first()
    if not admin_user or admin_user.role != UserRole.ADMIN or admin_user.status != UserStatus.ACTIVE:
        raise HTTPException(status_code=403, detail="Active ADMIN role required to inspect private evidence.")

    ev = db.query(EvidenceRecord).filter(EvidenceRecord.id == evidence_id).first()
    if not ev:
        raise HTTPException(status_code=404, detail="Evidence record not found.")

    file_bytes = verify_evidence_file_integrity(ev)

    safe_download_name = sanitize_filename(ev.original_filename, os.path.basename(ev.file_reference))
    db.add(
        AdminAction(
            admin_id=admin_user.id,
            action_type=AdminActionType.DOC_REVIEW,
            target_user_id=ev.user_id,
            notes=f"VIEWED_FILE: {ev.id} ({ev.evidence_type.value}, sha256={ev.file_sha256[:16]}...)",
        )
    )
    db.commit()

    return Response(
        content=file_bytes,
        media_type=ev.mime_type,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, private",
            "Content-Disposition": f'inline; filename="{safe_download_name}"',
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )