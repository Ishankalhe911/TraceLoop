import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from auth import RequireRole
from database import (
    AdminAction,
    AdminActionType,
    Device,
    DeviceStatus,
    RegistrationStatus,
    DocStatus,
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
    TIME_BOUND_CREDENTIALS,
    TIME_BOUND_ENFORCEMENT_ENABLED,
    assert_active_role_evidence,
    certificate_date_for_display,
    evidence_object_exists,
    parse_certificate_date,
    sync_expired_evidence_for_user,
    verify_evidence_file_integrity,
)
from services.ledger_service import ledger_service

logger = logging.getLogger("TraceLoop.AdminRoutes")
router = APIRouter(prefix="/admin", tags=["Admin"])

ROLE_CREDENTIAL_TYPES = {
    UserRole.VERIFIABLE: EvidenceType.BRAND_AUTHORIZATION,
    UserRole.RECYCLER: EvidenceType.RECYCLER_AUTHORIZATION,
}


def standard_response(data: dict = None, code: int = 200) -> dict:
    return {
        "status": "success",
        "code": code,
        "data": data or {},
        "error": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "traceloop_version": "v1",
    }


# --- REQUEST SCHEMAS ---
class ApprovalPayload(BaseModel):
    notes: Optional[str] = Field(default=None, max_length=3000)


class DocReviewPayload(BaseModel):
    decision: str = Field(description='"ACCEPT" or "REJECT"')
    rejection_reason: Optional[str] = Field(default=None, max_length=3000)
    review_notes: Optional[str] = Field(default=None, max_length=3000)
    # Reserved for the future certificate-expiry workflow. Time-bound enforcement is
    # intentionally disabled in this phase; do not make this field required in the UI.
    confirmed_expires_at: Optional[str] = None
    # Explicit UI attestation; the backend additionally requires a matching
    # VIEWED_FILE audit record for this admin and this evidence record.
    document_inspected: bool = False


class ExportDevicePayload(BaseModel):
    notes: Optional[str] = Field(default=None, max_length=3000)


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        # SQLite may return naive values for DateTime(timezone=True). This application
        # persists normalized UTC datetimes, so attach UTC rather than changing the instant.
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _get_linked_evidence(db: Session, legacy_doc_id: str) -> Optional[EvidenceRecord]:
    return (
        db.query(EvidenceRecord)
        .filter(EvidenceRecord.legacy_kyc_doc_id == legacy_doc_id)
        .order_by(EvidenceRecord.created_at.desc(), EvidenceRecord.id.desc())
        .first()
    )


def _get_latest_evidence(
    db: Session,
    user_id: str,
    evidence_type: EvidenceType,
) -> Optional[EvidenceRecord]:
    return (
        db.query(EvidenceRecord)
        .filter(
            EvidenceRecord.user_id == user_id,
            EvidenceRecord.evidence_type == evidence_type,
            EvidenceRecord.subject_type == EvidenceSubjectType.USER_ROLE_CREDENTIAL,
        )
        .order_by(EvidenceRecord.created_at.desc(), EvidenceRecord.id.desc())
        .first()
    )


def _admin_viewed_evidence(db: Session, admin_id: str, evidence: EvidenceRecord) -> bool:
    """Require an audit trail showing this admin opened the protected file viewer."""
    viewed_note_prefix = f"VIEWED_FILE: {evidence.id} ("
    return (
        db.query(AdminAction.id)
        .filter(
            AdminAction.admin_id == admin_id,
            AdminAction.target_user_id == evidence.user_id,
            AdminAction.action_type == AdminActionType.DOC_REVIEW,
            AdminAction.notes.contains(viewed_note_prefix),
        )
        .first()
        is not None
    )


def _set_user_active(user: User, admin: User) -> None:
    user.status = UserStatus.ACTIVE
    user.admin_approved = True
    user.admin_approved_by = admin.id
    user.admin_approved_at = datetime.now(timezone.utc)


def _append_note(existing: Optional[str], note: str) -> str:
    if not existing:
        return note[:4000]
    if note in existing:
        return existing[:4000]
    return f"{existing}\n{note}"[:4000]


def _review_evidence_record(
    evidence: EvidenceRecord,
    payload: DocReviewPayload,
    current_user: User,
    db: Session,
    linked_doc: Optional[KYCDocument] = None,
) -> dict:
    decision = (payload.decision or "").strip().upper()
    if decision not in {"ACCEPT", "REJECT"}:
        raise HTTPException(status_code=400, detail="Decision must be ACCEPT or REJECT.")
    if decision == "REJECT" and not (payload.rejection_reason or "").strip():
        raise HTTPException(status_code=400, detail="rejection_reason is required when rejecting evidence.")
    if evidence.verification_status != EvidenceVerificationStatus.PENDING:
        raise HTTPException(
            status_code=409,
            detail=f"Evidence has already been reviewed or is no longer pending: {evidence.verification_status.value}.",
        )

    if linked_doc is None and evidence.legacy_kyc_doc_id:
        linked_doc = db.query(KYCDocument).filter(KYCDocument.id == evidence.legacy_kyc_doc_id).first()
    if evidence.legacy_kyc_doc_id and linked_doc is None:
        raise HTTPException(
            status_code=409,
            detail="The linked legacy KYC row is missing. Resolve the record mismatch before review.",
        )
    if linked_doc is not None and linked_doc.status != DocStatus.PENDING:
        raise HTTPException(
            status_code=409,
            detail=(
                "The linked legacy KYC record is not pending. Resolve the mismatch before reviewing "
                "this evidence record."
            ),
        )

    owner = db.query(User).filter(User.id == evidence.user_id).first()
    if owner is None:
        raise HTTPException(status_code=404, detail="Evidence owner was not found.")

    now = datetime.now(timezone.utc)
    confirmed_expiry = None
    audit_detail = (payload.review_notes or "").strip()

    if decision == "ACCEPT":
        # Do not accept a digest alone: re-hash the actual stored file right before review.
        verify_evidence_file_integrity(evidence)

        # A reviewer must explicitly attest to inspecting the file, and the protected viewer
        # must have recorded that this admin opened this specific evidence record.
        if not payload.document_inspected:
            raise HTTPException(
                status_code=400,
                detail="Confirm that you inspected the document before accepting this evidence.",
            )
        if not _admin_viewed_evidence(db, current_user.id, evidence):
            raise HTTPException(
                status_code=400,
                detail="Open the protected document viewer for this evidence before accepting it.",
            )

        if TIME_BOUND_ENFORCEMENT_ENABLED and evidence.evidence_type in TIME_BOUND_CREDENTIALS:
            if not (payload.confirmed_expires_at or "").strip():
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "confirmed_expires_at is required for this certificate. Enter the expiry date "
                        "you verified on the inspected document (YYYY-MM-DD)."
                    ),
                )
            confirmed_expiry = parse_certificate_date(payload.confirmed_expires_at, end_of_day=True)
            if confirmed_expiry is None:
                raise HTTPException(status_code=400, detail="A valid confirmed_expires_at date is required.")
            if confirmed_expiry <= now:
                raise HTTPException(status_code=400, detail="The confirmed certificate expiry is already in the past.")
            issued_at = _as_utc(evidence.issued_at)
            if issued_at is not None and confirmed_expiry <= issued_at:
                raise HTTPException(status_code=400, detail="Expiry must be later than the issue date.")

            uploaded_claim = certificate_date_for_display(evidence.expires_at)
            confirmed_display = certificate_date_for_display(confirmed_expiry)
            audit_detail = _append_note(
                audit_detail or None,
                f"Admin-confirmed expiry: {confirmed_display}; uploader-claimed expiry: {uploaded_claim or 'not supplied'}.",
            )

    evidence.reviewed_by = current_user.id
    evidence.reviewed_at = now
    evidence.review_notes = audit_detail or (payload.rejection_reason or "").strip() or None

    if decision == "ACCEPT":
        evidence.verification_status = EvidenceVerificationStatus.VERIFIED
        if confirmed_expiry is not None:
            # The uploader's unverified claim is replaced only after the admin explicitly
            # confirms the value from the inspected certificate.
            evidence.expires_at = confirmed_expiry
        if linked_doc is not None:
            linked_doc.status = DocStatus.ACCEPTED
            linked_doc.reviewed_by = current_user.id
            linked_doc.reviewed_at = now
            linked_doc.rejection_reason = None

        # The reviewer/recycler role still requires a separate account-approval action.
        # Reseller business registration and first-buyer purchase proof keep their existing
        # onboarding activation behavior. Rejected renewal documents never globally reject
        # an otherwise active account.
        if (
            evidence.subject_type == EvidenceSubjectType.USER_ROLE_CREDENTIAL
            and owner.role == UserRole.BUYER
            and evidence.evidence_type == EvidenceType.IDENTITY_PROOF
            and owner.status == UserStatus.KYC_IN_PROGRESS
        ):
            # A normal BUYER becomes active only after admin accepts identity evidence.
            _set_user_active(owner, current_user)
        elif (
            evidence.subject_type == EvidenceSubjectType.USER_ROLE_CREDENTIAL
            and owner.role == UserRole.RESELLER
            and evidence.evidence_type == EvidenceType.BUSINESS_REGISTRATION
            and owner.status == UserStatus.KYC_IN_PROGRESS
        ):
            _set_user_active(owner, current_user)
        elif (
            evidence.subject_type == EvidenceSubjectType.DEVICE_CLAIM
            and owner.role == UserRole.FIRST_BUYER
            and evidence.evidence_type == EvidenceType.PURCHASE_PROOF
            and owner.status == UserStatus.KYC_IN_PROGRESS
        ):
            _set_user_active(owner, current_user)
        message = "Evidence verified. A separate account-approval step is still required where applicable."
    else:
        evidence.verification_status = EvidenceVerificationStatus.REJECTED
        if linked_doc is not None:
            linked_doc.status = DocStatus.REJECTED
            linked_doc.reviewed_by = current_user.id
            linked_doc.reviewed_at = now
            linked_doc.rejection_reason = (payload.rejection_reason or "").strip()
        # Leave the user status unchanged so corrected evidence can be resubmitted when
        # the account remains eligible. The upload route itself blocks SUSPENDED/REJECTED.
        message = "Evidence rejected. The user account status was not changed; corrected evidence may be submitted when eligible."

    action_notes = (
        f"EVIDENCE_{decision}: id={evidence.id}; type={evidence.evidence_type.value}; "
        f"subject={evidence.subject_id}; sha256={evidence.file_sha256[:16]}..."
    )
    if decision == "REJECT":
        action_notes += f"; reason={(payload.rejection_reason or '').strip()}"
    if payload.review_notes:
        action_notes += f"; notes={payload.review_notes.strip()}"
    db.add(
        AdminAction(
            admin_id=current_user.id,
            action_type=AdminActionType.DOC_REVIEW,
            target_user_id=evidence.user_id,
            notes=action_notes[:4000],
        )
    )
    db.commit()
    db.refresh(evidence)
    if linked_doc is not None:
        db.refresh(linked_doc)
    db.refresh(owner)

    return {
        "doc_id": linked_doc.id if linked_doc else evidence.id,
        "evidence_id": evidence.id,
        "legacy_kyc_doc_id": linked_doc.id if linked_doc else None,
        "decision": decision,
        "verification_status": evidence.verification_status.value,
        "subject_type": evidence.subject_type.value,
        "expires_at": certificate_date_for_display(evidence.expires_at),
        "user_id": owner.id,
        "user_status": owner.status.value,
        "message": message,
    }


# --- 1. ACCOUNT APPROVAL: VERIFIABLE + RECYCLER ---
@router.get("/approvals/pending")
def get_pending_approvals(
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.ADMIN])),
):
    pending_users = (
        db.query(User)
        .filter(
            User.status == UserStatus.KYC_IN_PROGRESS,
            User.role.in_([UserRole.VERIFIABLE, UserRole.RECYCLER]),
        )
        .order_by(User.created_at.asc())
        .all()
    )

    pending_accounts = []
    for user in pending_users:
        sync_expired_evidence_for_user(user, db)
        credential_type = ROLE_CREDENTIAL_TYPES[user.role]
        latest = _get_latest_evidence(db, user.id, credential_type)
        credential_status = latest.verification_status.value if latest else "MISSING"
        activation_stage = {
            "MISSING": "DOCUMENT_REQUIRED",
            "PENDING": "WAITING_FOR_DOCUMENT_REVIEW",
            "VERIFIED": "READY_FOR_ACCOUNT_APPROVAL",
            "REJECTED": "CORRECTION_REQUIRED",
            "EXPIRED": "CREDENTIAL_EXPIRED",
            "REVOKED": "CREDENTIAL_REVOKED",
        }.get(credential_status, "CREDENTIAL_UNAVAILABLE")
        pending_accounts.append(
            {
                # This is the one onboarding/activation request, derived from the user record.
                # Do not create a second request row when evidence is accepted; evidence status
                # simply moves this same request from waiting to ready for admin activation.
                "user_id": user.id,
                "name": user.name,
                "phone": user.phone,
                "role": user.role.value,
                "user_status": user.status.value,
                "brand_auth_code": user.brand_auth_code,
                "service_center_id": user.service_center_id,
                "cpcb_number": user.cpcb_number,
                "credential_evidence_id": latest.id if latest else None,
                "credential_status": credential_status,
                "activation_stage": activation_stage,
                "can_activate": credential_status == "VERIFIED",
                "credential_expires_at": certificate_date_for_display(latest.expires_at) if latest and TIME_BOUND_ENFORCEMENT_ENABLED else None,
                "created_at": user.created_at.isoformat() if user.created_at else None,
            }
        )
    return standard_response({"pending_accounts": pending_accounts})


@router.post("/approvals/{target_user_id}/approve")
def approve_account(
    target_user_id: str,
    payload: ApprovalPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.ADMIN])),
):
    target = db.query(User).filter(User.id == target_user_id).first()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found.")
    if target.role not in ROLE_CREDENTIAL_TYPES:
        raise HTTPException(status_code=400, detail="Only VERIFIABLE and RECYCLER require this approval step.")
    if target.status != UserStatus.KYC_IN_PROGRESS:
        raise HTTPException(
            status_code=409,
            detail=f"Account must be KYC_IN_PROGRESS for initial approval; current status is {target.status.value}.",
        )

    credential_type = ROLE_CREDENTIAL_TYPES[target.role]
    try:
        credential = assert_active_role_evidence(target, credential_type, db)
        # The canonical evidence helper enforces expiry/revocation. This check also confirms
        # the underlying file still exists and matches the digest before activation.
        verify_evidence_file_integrity(credential)
    except HTTPException:
        db.rollback()
        raise

    try:
        _set_user_active(target, current_user)
        db.add(
            AdminAction(
                admin_id=current_user.id,
                action_type=AdminActionType.APPROVE,
                target_user_id=target.id,
                notes=(payload.notes or f"Account approved using evidence {credential.id}.")[:4000],
            )
        )
        db.commit()
        return standard_response(
            {
                "target_user_id": target.id,
                "new_status": target.status.value,
                "credential_evidence_id": credential.id,
                "message": f"{target.role.value} account approved and activated.",
            }
        )
    except Exception:
        db.rollback()
        logger.exception("Failed to approve account %s", target_user_id)
        raise HTTPException(status_code=500, detail="Account approval failed. Check server logs.")


@router.post("/approvals/{target_user_id}/reject")
def reject_account(
    target_user_id: str,
    payload: ApprovalPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.ADMIN])),
):
    target = db.query(User).filter(User.id == target_user_id).first()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found.")
    if target.role not in ROLE_CREDENTIAL_TYPES:
        raise HTTPException(status_code=400, detail="Only VERIFIABLE and RECYCLER can be rejected here.")
    if target.status != UserStatus.KYC_IN_PROGRESS:
        raise HTTPException(status_code=409, detail="Only accounts in KYC_IN_PROGRESS can be rejected here.")

    try:
        target.status = UserStatus.REJECTED
        target.admin_approved = False
        target.admin_approved_by = current_user.id
        target.admin_approved_at = datetime.now(timezone.utc)
        db.add(
            AdminAction(
                admin_id=current_user.id,
                action_type=AdminActionType.REJECT,
                target_user_id=target.id,
                notes=(payload.notes or "Account rejected by administrator.")[:4000],
            )
        )
        db.commit()
        return standard_response(
            {
                "target_user_id": target.id,
                "new_status": target.status.value,
                "message": "Account onboarding rejected. An administrator must explicitly reopen it before further uploads are permitted.",
            }
        )
    except Exception:
        db.rollback()
        logger.exception("Failed to reject account %s", target_user_id)
        raise HTTPException(status_code=500, detail="Account rejection failed. Check server logs.")


# --- 2. UNIFIED KYC / EVIDENCE REVIEW QUEUE ---
@router.get("/kyc/pending")
def get_pending_documents(
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.ADMIN])),
):
    results = []
    included_evidence_ids = set()

    pending_legacy = (
        db.query(KYCDocument)
        .filter(KYCDocument.status == DocStatus.PENDING)
        .order_by(KYCDocument.uploaded_at.asc())
        .all()
    )
    for legacy in pending_legacy:
        evidence = _get_linked_evidence(db, legacy.id)
        if evidence is not None:
            included_evidence_ids.add(evidence.id)
        owner = db.query(User).filter(User.id == legacy.user_id).first()
        file_exists = evidence_object_exists(evidence.file_reference) if evidence else False
        view_url = None
        if evidence is not None and file_exists:
            # Plain endpoint path only. The frontend fetches it with the admin Bearer JWT.
            view_url = f"/admin/evidence/{evidence.id}/view"
        results.append(
            {
                "doc_id": legacy.id,
                "evidence_id": evidence.id if evidence else None,
                "legacy_kyc_doc_id": legacy.id,
                "user_id": legacy.user_id,
                "user_name": owner.name if owner else None,
                "user_phone": owner.phone if owner else None,
                "user_role": owner.role.value if owner else None,
                "user_status": owner.status.value if owner else None,
                "doc_type": evidence.evidence_type.value if evidence else legacy.doc_type.value,
                "subject_type": evidence.subject_type.value if evidence else "LEGACY_HASH_ONLY",
                "subject_id": evidence.subject_id if evidence else legacy.serial_for_device or legacy.user_id,
                "serial_for_device": legacy.serial_for_device,
                "claimed_document_number": evidence.claimed_document_number if evidence else None,
                "claimed_issuer": evidence.claimed_issuer if evidence else None,
                "issued_at": certificate_date_for_display(evidence.issued_at) if evidence else None,
                "expires_at_claimed": certificate_date_for_display(evidence.expires_at) if evidence else None,
                "requires_expiry": bool(TIME_BOUND_ENFORCEMENT_ENABLED and evidence and evidence.evidence_type in TIME_BOUND_CREDENTIALS),
                "original_filename": evidence.original_filename if evidence else None,
                "mime_type": evidence.mime_type if evidence else None,
                "file_hash": evidence.file_sha256 if evidence else legacy.file_hash,
                "has_stored_file": file_exists,
                "can_approve": bool(
                    evidence
                    and evidence.verification_status == EvidenceVerificationStatus.PENDING
                    and file_exists
                ),
                "is_legacy_hash_only": evidence is None,
                "view_url": view_url,
                "protected_view_url": view_url,
                "status": evidence.verification_status.value if evidence else legacy.status.value,
                "uploaded_at": evidence.created_at.isoformat() if evidence and evidence.created_at else (
                    legacy.uploaded_at.isoformat() if legacy.uploaded_at else None
                ),
            }
        )

    # Include every other pending EvidenceRecord, including standalone identity evidence
    # and any rows whose linked legacy status is inconsistent. Such inconsistencies remain
    # non-approvable until the underlying linked record is reconciled.
    pending_evidence = (
        db.query(EvidenceRecord)
        .filter(EvidenceRecord.verification_status == EvidenceVerificationStatus.PENDING)
        .order_by(EvidenceRecord.created_at.asc())
        .all()
    )
    for evidence in pending_evidence:
        if evidence.id in included_evidence_ids:
            continue
        owner = db.query(User).filter(User.id == evidence.user_id).first()
        linked_doc = (
            db.query(KYCDocument).filter(KYCDocument.id == evidence.legacy_kyc_doc_id).first()
            if evidence.legacy_kyc_doc_id
            else None
        )
        file_exists = evidence_object_exists(evidence.file_reference)
        view_url = None
        if file_exists:
            # Plain endpoint path only. The frontend fetches it with the admin Bearer JWT.
            view_url = f"/admin/evidence/{evidence.id}/view"
        legacy_pending_or_standalone = (
            not evidence.legacy_kyc_doc_id
            or (linked_doc is not None and linked_doc.status == DocStatus.PENDING)
        )
        results.append(
            {
                "doc_id": linked_doc.id if linked_doc else evidence.id,
                "evidence_id": evidence.id,
                "legacy_kyc_doc_id": evidence.legacy_kyc_doc_id,
                "user_id": evidence.user_id,
                "user_name": owner.name if owner else None,
                "user_phone": owner.phone if owner else None,
                "user_role": owner.role.value if owner else None,
                "user_status": owner.status.value if owner else None,
                "doc_type": evidence.evidence_type.value,
                "subject_type": evidence.subject_type.value,
                "subject_id": evidence.subject_id,
                "serial_for_device": evidence.subject_id if evidence.subject_type == EvidenceSubjectType.DEVICE_CLAIM else None,
                "claimed_document_number": evidence.claimed_document_number,
                "claimed_issuer": evidence.claimed_issuer,
                "issued_at": certificate_date_for_display(evidence.issued_at),
                "expires_at_claimed": certificate_date_for_display(evidence.expires_at),
                "requires_expiry": bool(TIME_BOUND_ENFORCEMENT_ENABLED and evidence.evidence_type in TIME_BOUND_CREDENTIALS),
                "original_filename": evidence.original_filename,
                "mime_type": evidence.mime_type,
                "file_hash": evidence.file_sha256,
                "has_stored_file": file_exists,
                "can_approve": bool(file_exists and legacy_pending_or_standalone),
                "is_legacy_hash_only": False,
                "view_url": view_url,
                "protected_view_url": view_url,
                "status": evidence.verification_status.value,
                "legacy_status": linked_doc.status.value if linked_doc else None,
                "uploaded_at": evidence.created_at.isoformat() if evidence.created_at else None,
            }
        )

    results.sort(key=lambda item: item.get("uploaded_at") or "")
    return standard_response({"pending_documents": results, "total": len(results)})


@router.post("/evidence/{evidence_id}/review")
def review_evidence(
    evidence_id: str,
    payload: DocReviewPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.ADMIN])),
):
    evidence = db.query(EvidenceRecord).filter(EvidenceRecord.id == evidence_id).first()
    if evidence is None:
        raise HTTPException(status_code=404, detail="Evidence record not found.")
    linked_doc = (
        db.query(KYCDocument).filter(KYCDocument.id == evidence.legacy_kyc_doc_id).first()
        if evidence.legacy_kyc_doc_id
        else None
    )
    try:
        return standard_response(_review_evidence_record(evidence, payload, current_user, db, linked_doc))
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Evidence review failed for evidence %s", evidence_id)
        raise HTTPException(status_code=500, detail="Evidence review failed. Check server logs.")


@router.post("/kyc/{doc_id}/review")
def review_kyc_document(
    doc_id: str,
    payload: DocReviewPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.ADMIN])),
):
    """Compatibility endpoint for the existing admin frontend; accepts a legacy or evidence ID."""
    legacy_doc = db.query(KYCDocument).filter(KYCDocument.id == doc_id).first()
    if legacy_doc is None:
        # Optional identity evidence may not have a legacy KYCDocument row.
        evidence = db.query(EvidenceRecord).filter(EvidenceRecord.id == doc_id).first()
        if evidence is None:
            raise HTTPException(status_code=404, detail="Document or evidence record not found.")
        linked_doc = (
            db.query(KYCDocument).filter(KYCDocument.id == evidence.legacy_kyc_doc_id).first()
            if evidence.legacy_kyc_doc_id
            else None
        )
        try:
            return standard_response(_review_evidence_record(evidence, payload, current_user, db, linked_doc))
        except HTTPException:
            db.rollback()
            raise
        except Exception:
            db.rollback()
            logger.exception("Evidence review failed for %s", doc_id)
            raise HTTPException(status_code=500, detail="Evidence review failed. Check server logs.")

    if legacy_doc.status != DocStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"Legacy KYC document is not pending: {legacy_doc.status.value}.")

    evidence = _get_linked_evidence(db, legacy_doc.id)
    decision = (payload.decision or "").strip().upper()
    if decision not in {"ACCEPT", "REJECT"}:
        raise HTTPException(status_code=400, detail="Decision must be ACCEPT or REJECT.")
    if decision == "REJECT" and not (payload.rejection_reason or "").strip():
        raise HTTPException(status_code=400, detail="rejection_reason is required when rejecting evidence.")

    if evidence is None:
        if decision == "ACCEPT":
            raise HTTPException(
                status_code=409,
                detail=(
                    "This legacy KYC row has no linked server-verified EvidenceRecord. The user must re-upload "
                    "the actual file through /api/v1/evidence/upload before it can be accepted."
                ),
            )
        try:
            legacy_doc.status = DocStatus.REJECTED
            legacy_doc.rejection_reason = (payload.rejection_reason or "").strip()
            legacy_doc.reviewed_by = current_user.id
            legacy_doc.reviewed_at = datetime.now(timezone.utc)
            db.add(
                AdminAction(
                    admin_id=current_user.id,
                    action_type=AdminActionType.DOC_REVIEW,
                    target_user_id=legacy_doc.user_id,
                    notes=(
                        f"LEGACY_KYC_REJECTED_WITHOUT_SERVER_VERIFIED_EVIDENCE: {legacy_doc.id}; "
                        f"{(payload.rejection_reason or '').strip()}"
                    )[:4000],
                )
            )
            # Do not change the account status; the hash-only row cannot establish document validity.
            db.commit()
            return standard_response(
                {
                    "doc_id": legacy_doc.id,
                    "decision": "REJECT",
                    "message": "Legacy hash-only document rejected. The account status was not changed.",
                }
            )
        except Exception:
            db.rollback()
            logger.exception("Legacy KYC rejection failed for document %s", doc_id)
            raise HTTPException(status_code=500, detail="Document rejection failed. Check server logs.")

    try:
        return standard_response(_review_evidence_record(evidence, payload, current_user, db, legacy_doc))
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("KYC review failed for document %s", doc_id)
        raise HTTPException(status_code=500, detail="Document review failed. Check server logs.")


# --- 3. DEVICE EXPORT & OVERSIGHT ---
@router.post("/devices/{device_id}/export")
def export_device(
    device_id: str,
    payload: ExportDevicePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.ADMIN])),
):
    device = db.query(Device).filter(Device.public_id == device_id).first()
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found.")
    if device.registration_status != RegistrationStatus.CONFIRMED:
        raise HTTPException(
            status_code=409,
            detail="Device has no confirmed Phase 2 on-chain registration; export is blocked.",
        )
    if device.status in {DeviceStatus.RECYCLED, DeviceStatus.EXPORTED}:
        raise HTTPException(status_code=409, detail=f"Device is already in terminal state: {device.status.value}.")

    try:
        chain_result = ledger_service.mark_exported(public_id=device.public_id)
        device.status = DeviceStatus.EXPORTED
        device.stamp_valid = False
        db.add(
            AdminAction(
                admin_id=current_user.id,
                action_type=AdminActionType.REVOKE,
                target_device_id=device.id,
                notes=(payload.notes or "Device exported by administrator.")[:4000],
            )
        )
        db.commit()
        return standard_response(
            {
                "device_id": device.public_id,
                "status": DeviceStatus.EXPORTED.value,
                "chain_tx_id": chain_result["tx_id"],
                "message": "Device permanently exported on-chain.",
            }
        )
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Export failed for device %s", device_id)
        raise HTTPException(status_code=500, detail="Export failed. Check server logs.")


@router.get("/devices")
def get_all_devices(
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.ADMIN])),
):
    devices = db.query(Device).order_by(Device.registered_at.desc()).all()
    return standard_response(
        {
            "total": len(devices),
            "devices": [
                {
                    "device_id": device.public_id,
                    "brand": device.brand_name,
                    "status": device.status.value,
                    "stamp_valid": device.stamp_valid,
                    "current_owner_id": device.current_owner_id,
                    "is_for_sale": device.is_for_sale,
                    "registered_at": device.registered_at.isoformat() if device.registered_at else None,
                }
                for device in devices
            ],
        }
    )


@router.get("/verifiers")
def get_verifiers(
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.ADMIN])),
):
    candidates = (
        db.query(User)
        .filter(User.role == UserRole.VERIFIABLE, User.status == UserStatus.ACTIVE)
        .all()
    )
    verifiers = []
    status_changed = False
    for verifier in candidates:
        try:
            credential = assert_active_role_evidence(verifier, EvidenceType.BRAND_AUTHORIZATION, db)
            verify_evidence_file_integrity(credential)
        except HTTPException:
            # Fail closed: if the active verifier has no currently valid, intact credential,
            # remove active privileges until an admin verifies a new credential.
            if verifier.status == UserStatus.ACTIVE:
                verifier.status = UserStatus.KYC_IN_PROGRESS
                verifier.admin_approved = False
                status_changed = True
            continue

        verifiers.append(
            {
                "user_id": verifier.id,
                "name": verifier.name,
                "phone": verifier.phone,
                "status": verifier.status.value,
                "dispute_count": verifier.dispute_count,
                "service_center_id": verifier.service_center_id,
                "brand_auth_code": verifier.brand_auth_code,
                "approved_at": verifier.admin_approved_at.isoformat() if verifier.admin_approved_at else None,
                "credential_evidence_id": credential.id,
                "credential_expires_at": certificate_date_for_display(credential.expires_at),
            }
        )

    if status_changed:
        db.commit()
    return standard_response({"total": len(verifiers), "verifiers": verifiers})


@router.post("/verifiers/{verifier_id}/revoke")
def revoke_verifier(
    verifier_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.ADMIN])),
):
    verifier = (
        db.query(User)
        .filter(User.id == verifier_id, User.role == UserRole.VERIFIABLE)
        .first()
    )
    if verifier is None:
        raise HTTPException(status_code=404, detail="Verifier not found.")
    if verifier.status == UserStatus.SUSPENDED:
        raise HTTPException(status_code=409, detail="Verifier is already suspended.")

    try:
        now = datetime.now(timezone.utc)
        verifier.status = UserStatus.SUSPENDED
        verifier.admin_approved = False
        credentials = (
            db.query(EvidenceRecord)
            .filter(
                EvidenceRecord.user_id == verifier.id,
                EvidenceRecord.evidence_type == EvidenceType.BRAND_AUTHORIZATION,
                EvidenceRecord.subject_type == EvidenceSubjectType.USER_ROLE_CREDENTIAL,
                EvidenceRecord.verification_status == EvidenceVerificationStatus.VERIFIED,
            )
            .all()
        )
        for credential in credentials:
            credential.verification_status = EvidenceVerificationStatus.REVOKED
            credential.reviewed_by = current_user.id
            credential.reviewed_at = now
            credential.review_notes = _append_note(
                credential.review_notes,
                "Revoked by administrator through verifier oversight.",
            )

        db.add(
            AdminAction(
                admin_id=current_user.id,
                action_type=AdminActionType.REVOKE,
                target_user_id=verifier.id,
                notes=f"Verifier suspended and {len(credentials)} verified BRAND_AUTHORIZATION credential(s) revoked.",
            )
        )
        db.commit()
        return standard_response(
            {
                "user_id": verifier.id,
                "status": verifier.status.value,
                "revoked_credentials": len(credentials),
                "message": "Verifier suspended and verified brand authorization credentials revoked.",
            }
        )
    except Exception:
        db.rollback()
        logger.exception("Failed to revoke verifier %s", verifier_id)
        raise HTTPException(status_code=500, detail="Verifier revocation failed. Check server logs.")
