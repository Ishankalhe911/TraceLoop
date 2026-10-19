import logging
import os
import random
from datetime import datetime, timezone
from typing import Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, root_validator
from sqlalchemy.orm import Session

from auth import (
    create_otp_session,
    get_current_user_any_status,
    issue_tokens,
    refresh_access_token,
    verify_otp_session,
)
from database import (
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
from routes.evidence_routes import (
    certificate_date_for_display,
    sync_expired_evidence_for_user,
)

logger = logging.getLogger("TraceLoop.UserRoutes")
router = APIRouter(prefix="/api/v1", tags=["Auth & Users"])


def standard_response(data: dict = None, code: int = 200) -> dict:
    return {
        "status": "success",
        "code": code,
        "data": data or {},
        "error": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "traceloop_version": "v1",
    }


# --- SCHEMAS ---
class RegisterUserPayload(BaseModel):
    phone: str = Field(..., max_length=15)
    name: str = Field(..., max_length=120)
    role: UserRole
    aadhaar_last4: str = Field(..., min_length=4, max_length=4)
    gst_number: Optional[str] = None
    cpcb_number: Optional[str] = None
    brand_auth_code: Optional[str] = None
    service_center_id: Optional[str] = None

    @root_validator(skip_on_failure=True)
    def validate_role_requirements(cls, values):
        role = values.get("role")
        if role == UserRole.ADMIN:
            raise ValueError("Admin role cannot be self-assigned.")
        if role == UserRole.RESELLER and not values.get("gst_number"):
            raise ValueError("GST number is mandatory for RESELLER.")
        if role == UserRole.RECYCLER and not values.get("cpcb_number"):
            raise ValueError("CPCB number is mandatory for RECYCLER.")
        if role == UserRole.VERIFIABLE:
            if not values.get("brand_auth_code") or not values.get("service_center_id"):
                raise ValueError("brand_auth_code and service_center_id are mandatory for VERIFIABLE.")
        return values


class OTPSendPayload(BaseModel):
    phone: str = Field(..., max_length=15)


class OTPVerifyPayload(BaseModel):
    phone: str = Field(..., max_length=15)
    otp_code: str = Field(..., min_length=4, max_length=6)


class RefreshTokenPayload(BaseModel):
    refresh_token: str


# Used only to keep the legacy hash-only endpoint's request schema documented.
# The endpoint below intentionally rejects submissions; only actual file bytes may be uploaded.
class LegacyKYCUploadPayload(BaseModel):
    doc_type: DocType
    file_hash: str = Field(..., min_length=64, max_length=64)
    serial_for_device: Optional[str] = Field(None, max_length=80)


# Legacy display names are retained for older dashboard components, while `evidence_type`
# and `verification_status` expose the current Phase 1 model to updated frontend code.
_EVIDENCE_DISPLAY_DOC_TYPE = {
    EvidenceType.PURCHASE_PROOF: DocType.PURCHASE_PROOF.value,
    EvidenceType.BUSINESS_REGISTRATION: DocType.BUSINESS_PROOF.value,
    EvidenceType.BRAND_AUTHORIZATION: DocType.BRAND_AUTH.value,
    EvidenceType.RECYCLER_AUTHORIZATION: DocType.CPCB_CERT.value,
    EvidenceType.IDENTITY_PROOF: "IDENTITY_PROOF",
    EvidenceType.RECYCLING_RECOVERY_PROOF: "RECYCLING_RECOVERY_PROOF",
}

_EVIDENCE_TO_LEGACY_STATUS = {
    EvidenceVerificationStatus.PENDING: DocStatus.PENDING.value,
    EvidenceVerificationStatus.VERIFIED: DocStatus.ACCEPTED.value,
    EvidenceVerificationStatus.REJECTED: DocStatus.REJECTED.value,
    EvidenceVerificationStatus.EXPIRED: EvidenceVerificationStatus.EXPIRED.value,
    EvidenceVerificationStatus.REVOKED: EvidenceVerificationStatus.REVOKED.value,
}


def _enum_value(value) -> Optional[str]:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


def _iso_datetime(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    # SQLAlchemy/SQLite may return naive datetimes for timezone=True columns.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


# --- REGISTRATION & OTP ---
@router.post("/register", status_code=status.HTTP_201_CREATED)
def register_user(payload: RegisterUserPayload, db: Session = Depends(get_db)):
    if db.query(User).filter(User.phone == payload.phone).first():
        raise HTTPException(status_code=409, detail="Phone number already registered.")

    try:
        new_user = User(
            phone=payload.phone,
            name=payload.name,
            role=payload.role,
            aadhaar_last4=payload.aadhaar_last4,
            gst_number=payload.gst_number,
            cpcb_number=payload.cpcb_number,
            brand_auth_code=payload.brand_auth_code,
            service_center_id=payload.service_center_id,
            status=UserStatus.PENDING,
            admin_approved=False,
            aadhaar_verified=False,
        )
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
        return standard_response(
            {
                "user_id": new_user.id,
                "role": new_user.role.value,
                "status": new_user.status.value,
                "message": "Registration successful. Call POST /api/v1/otp/send to receive your OTP.",
            },
            201,
        )
    except Exception:
        db.rollback()
        logger.exception("Registration failed")
        raise HTTPException(status_code=500, detail="Registration failed due to an internal server error.")


@router.post("/otp/send")
def send_otp(payload: OTPSendPayload, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.phone == payload.phone).first()
    if not user:
        raise HTTPException(status_code=404, detail="Phone number not registered.")
    if user.status in {UserStatus.SUSPENDED, UserStatus.REJECTED}:
        raise HTTPException(
            status_code=403,
            detail=f"Account is {user.status.value}. Cannot send OTP.",
        )

    otp = str(random.randint(100000, 999999))
    create_otp_session(phone=payload.phone, otp=otp, db=db)
    logger.info("OTP generated for registered phone account ending %s", payload.phone[-4:])

    # SMS fallback and OTP preview are separate decisions. A failed/missing SMS
    # provider must not automatically disclose an OTP in production responses.
    fast2sms_key = os.getenv("FAST2SMS_API_KEY")
    otp_preview_enabled = os.getenv("OTP_PREVIEW_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    sms_sent_successfully = False

    if fast2sms_key:
        try:
            clean_phone = "".join(filter(str.isdigit, payload.phone))[-10:]
            response = requests.get(
                "https://www.fast2sms.com/dev/bulkV2",
                headers={"cache-control": "no-cache"},
                params={
                    "authorization": fast2sms_key,
                    "variables_values": otp,
                    "route": "otp",
                    "numbers": clean_phone,
                },
                timeout=10,
            )
            if response.status_code == 200:
                sms_sent_successfully = True
            else:
                logger.error("Fast2SMS API failed with HTTP status %s", response.status_code)
        except requests.RequestException:
            logger.exception("Fast2SMS request failed")
    else:
        logger.warning("FAST2SMS_API_KEY is missing; SMS delivery is unavailable.")

    demo_preview_available = bool(not sms_sent_successfully and otp_preview_enabled)
    if not sms_sent_successfully and not otp_preview_enabled:
        logger.warning("OTP preview is disabled; configure SMS delivery or explicitly enable OTP_PREVIEW_ENABLED for a controlled demo.")

    return standard_response(
        {
            "message": (
                "OTP sent successfully."
                if sms_sent_successfully
                else "Demo OTP generated; use the preview below."
                if demo_preview_available
                else "OTP was generated, but SMS delivery was not confirmed. Contact the administrator if you cannot receive the code."
            ),
            "otp_preview": otp if demo_preview_available else None,
            "demo_otp_enabled": demo_preview_available,
            "delivery_confirmed": sms_sent_successfully,
            "expires_in_minutes": 10,
        }
    )


@router.post("/otp/verify")
def verify_otp(payload: OTPVerifyPayload, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.phone == payload.phone).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    if user.status in {UserStatus.SUSPENDED, UserStatus.REJECTED}:
        raise HTTPException(status_code=403, detail=f"Account is {user.status.value}.")

    verify_otp_session(phone=payload.phone, otp_code=payload.otp_code, db=db)

    if user.status == UserStatus.PENDING:
        # OTP verifies the phone number only. Every self-registered non-admin role must
        # submit its role-specific evidence and receive the required admin decision before
        # gaining access to protected TraceLoop functionality.
        if user.role == UserRole.ADMIN:
            raise HTTPException(status_code=403, detail="Admin accounts cannot be onboarded through public registration.")
        user.status = UserStatus.KYC_IN_PROGRESS
    db.commit()

    tokens = issue_tokens(user=user, db=db)
    message = (
        "OTP verified. Signed in successfully."
        if user.status == UserStatus.ACTIVE
        else "OTP verified. Upload the required evidence document and await review/approval."
    )
    return standard_response(
        {
            "user_id": user.id,
            "role": user.role.value,
            "status": user.status.value,
            "message": message,
            **tokens,
        }
    )


@router.post("/token/refresh")
def refresh_token(payload: RefreshTokenPayload, db: Session = Depends(get_db)):
    try:
        return standard_response(refresh_access_token(refresh_token=payload.refresh_token, db=db))
    except HTTPException:
        raise
    except Exception:
        logger.exception("Token refresh failed")
        raise HTTPException(status_code=500, detail="Token refresh failed due to an internal server error.")


# --- LEGACY KYC UPLOAD: DISABLED ---
@router.post("/kyc/upload")
def upload_kyc_document(
    payload: LegacyKYCUploadPayload,
    current_user: User = Depends(get_current_user_any_status),
):
    """Reject client-hash-only submissions; actual multipart file bytes are required.

    This compatibility endpoint deliberately does not create a KYCDocument row. Until the
    frontend is migrated, the old JSON-only upload UI will receive HTTP 410 and a clear
    instruction to use `/api/v1/evidence/upload` with multipart FormData.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=(
            "Hash-only KYC upload has been retired. Upload the actual PDF/JPEG/PNG/WebP file "
            "to /api/v1/evidence/upload using multipart FormData."
        ),
    )


# --- PROFILE & ACCOUNT ---
@router.get("/me")
def get_my_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_any_status),
):
    """Return the signed-in user's profile and synchronize expired role credentials."""
    sync_expired_evidence_for_user(current_user, db)
    # Refresh in case the expiry helper changed account status in the database.
    db.refresh(current_user)
    return standard_response(
        {
            "user_id": current_user.id,
            "name": current_user.name,
            "phone": current_user.phone,
            "role": current_user.role.value,
            "status": current_user.status.value,
            "admin_approved": current_user.admin_approved,
            "aadhaar_verified": current_user.aadhaar_verified,
            "reward_points": current_user.reward_points,
            "dispute_count": current_user.dispute_count,
        }
    )


@router.delete("/me", status_code=status.HTTP_200_OK)
def delete_my_account(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_any_status),
):
    """Legacy self-delete endpoint retained outside this KYC integration change.

    Note: deleting a user with linked devices, KYC/evidence, transfers, or audit rows may
    be blocked by foreign-key constraints. This route does not rewrite or delete immutable
    on-chain history, and it should not be treated as a complete erasure/anonymization flow.
    """
    try:
        db.delete(current_user)
        db.commit()
        return standard_response({
            "message": (
                "The account row was deleted. Immutable on-chain history was not changed; "
                "linked database records may prevent deletion and require a reviewed erasure workflow."
            )
        })
    except Exception:
        db.rollback()
        logger.exception("Account deletion failed for user %s", current_user.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Account deletion failed. Linked records may require a reviewed erasure workflow.",
        )


# --- EVIDENCE / KYC HISTORY ---
@router.get("/kyc/my-documents")
def get_my_kyc_documents(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_any_status),
):
    """Return evidence-first KYC history plus unbridged legacy rows, without leaking file paths.

    `status` maps VERIFIED to legacy `ACCEPTED` for the current notification UI; the exact
    Phase 1 value is also included as `verification_status`. Linked legacy rows are emitted
    only once, through their EvidenceRecord, to avoid duplicate notifications.
    """
    sync_expired_evidence_for_user(current_user, db)

    evidence_rows = (
        db.query(EvidenceRecord)
        .filter(EvidenceRecord.user_id == current_user.id)
        .order_by(EvidenceRecord.created_at.desc(), EvidenceRecord.id.desc())
        .all()
    )
    linked_legacy_ids = {
        row.legacy_kyc_doc_id for row in evidence_rows if row.legacy_kyc_doc_id
    }

    documents = []
    for ev in evidence_rows:
        legacy_doc = None
        if ev.legacy_kyc_doc_id:
            legacy_doc = (
                db.query(KYCDocument)
                .filter(KYCDocument.id == ev.legacy_kyc_doc_id, KYCDocument.user_id == current_user.id)
                .first()
            )

        rejection_reason = None
        if ev.verification_status == EvidenceVerificationStatus.REJECTED:
            rejection_reason = (
                (legacy_doc.rejection_reason if legacy_doc else None)
                or ev.review_notes
            )

        documents.append(
            {
                # `doc_id` remains compatible with the current UI; `evidence_id` is canonical.
                "doc_id": ev.legacy_kyc_doc_id or ev.id,
                "evidence_id": ev.id,
                "legacy_kyc_doc_id": ev.legacy_kyc_doc_id,
                "doc_type": _EVIDENCE_DISPLAY_DOC_TYPE.get(ev.evidence_type, ev.evidence_type.value),
                "evidence_type": ev.evidence_type.value,
                "subject_type": ev.subject_type.value,
                "subject_id": ev.subject_id,
                "status": _EVIDENCE_TO_LEGACY_STATUS.get(ev.verification_status, ev.verification_status.value),
                "verification_status": ev.verification_status.value,
                "serial_for_device": ev.subject_id if ev.subject_type == EvidenceSubjectType.DEVICE_CLAIM else None,
                "rejection_reason": rejection_reason,
                "expires_at": certificate_date_for_display(ev.expires_at),
                "uploaded_at": _iso_datetime(ev.created_at),
                "reviewed_at": _iso_datetime(ev.reviewed_at),
            }
        )

    # Keep old rows visible for history, but never present a legacy hash as verified evidence.
    legacy_rows = (
        db.query(KYCDocument)
        .filter(KYCDocument.user_id == current_user.id)
        .order_by(KYCDocument.uploaded_at.desc(), KYCDocument.id.desc())
        .all()
    )
    for doc in legacy_rows:
        if doc.id in linked_legacy_ids:
            continue
        documents.append(
            {
                "doc_id": doc.id,
                "evidence_id": None,
                "legacy_kyc_doc_id": doc.id,
                "doc_type": _enum_value(doc.doc_type),
                "evidence_type": None,
                "subject_type": "LEGACY_HASH_ONLY",
                "subject_id": doc.serial_for_device or doc.user_id,
                "status": _enum_value(doc.status),
                "verification_status": None,
                "serial_for_device": doc.serial_for_device,
                "rejection_reason": doc.rejection_reason,
                "expires_at": None,
                "uploaded_at": _iso_datetime(doc.uploaded_at),
                "reviewed_at": _iso_datetime(doc.reviewed_at),
            }
        )

    documents.sort(key=lambda item: item.get("uploaded_at") or "", reverse=True)
    return standard_response({"documents": documents, "total": len(documents)})
