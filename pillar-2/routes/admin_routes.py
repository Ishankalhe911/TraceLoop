from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database import (
    get_db, User, UserRole, UserStatus,
    AdminAction, AdminActionType,
    Device, DeviceStatus,
    KYCDocument, DocType, DocStatus
)
from auth import RequireRole, get_current_user
from services.ledger_service import ledger_service

router = APIRouter(prefix="/admin", tags=["Admin"])

def standard_response(data: dict = None, code: int = 200) -> dict:
    return {
        "status": "success", "code": code, "data": data or {},
        "error": None, "timestamp": datetime.utcnow().isoformat() + "Z",
        "traceloop_version": "v1"
    }

# --- SCHEMAS ---
class ApprovalPayload(BaseModel):
    notes: str = None

class DocReviewPayload(BaseModel):
    decision: str  # "ACCEPT" or "REJECT"
    rejection_reason: str = None

class ExportDevicePayload(BaseModel):
    notes: str = None


# --- 1. ACCOUNT APPROVAL (VERIFIABLE + RECYCLER) ---

@router.get("/approvals/pending")
def get_pending_approvals(
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.ADMIN]))
):
    """Fetches all VERIFIABLE and RECYCLER accounts awaiting manual approval."""
    pending_users = db.query(User).filter(
        User.status == UserStatus.KYC_IN_PROGRESS,
        User.role.in_([UserRole.VERIFIABLE, UserRole.RECYCLER])
    ).all()

    return standard_response({
        "pending_accounts": [
            {
                "user_id": u.id,
                "name": u.name,
                "role": u.role,
                "brand_auth_code": u.brand_auth_code,
                "cpcb_number": u.cpcb_number,
                "created_at": u.created_at.isoformat() + "Z"
            } for u in pending_users
        ]
    })


@router.post("/approvals/{target_user_id}/approve")
def approve_account(
    target_user_id: str,
    payload: ApprovalPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.ADMIN]))
):
    """Approves a VERIFIABLE or RECYCLER account → ACTIVE."""
    target_user = db.query(User).filter(User.id == target_user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found.")
    if target_user.role not in [UserRole.VERIFIABLE, UserRole.RECYCLER]:
        raise HTTPException(status_code=400, detail="Only VERIFIABLE and RECYCLER require admin approval.")
    if target_user.status == UserStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Account is already active.")

    try:
        target_user.status = UserStatus.ACTIVE
        target_user.admin_approved = True
        target_user.admin_approved_by = current_user.id
        target_user.admin_approved_at = datetime.utcnow()

        audit_log = AdminAction(
            admin_id=current_user.id,
            action_type=AdminActionType.APPROVE,
            target_user_id=target_user.id,
            notes=payload.notes
        )
        db.add(audit_log)
        db.commit()

        return standard_response({
            "target_user_id": target_user.id,
            "new_status": target_user.status,
            "message": f"{target_user.role} account approved and activated."
        })
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# --- 2. KYC DOCUMENT REVIEW (FIRST_BUYER + RESELLER) ---
@router.get("/kyc/pending")
def get_pending_documents(
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.ADMIN]))
):
    """Admin sees all KYC documents awaiting review."""
    pending_docs = db.query(KYCDocument).filter(
        KYCDocument.status == DocStatus.PENDING
    ).all()

    return standard_response({
        "pending_documents": [
            {
                "doc_id": d.id,
                "user_id": d.user_id,
                "doc_type": d.doc_type,
                "file_hash": d.file_hash,
                # THIS FIXES THE MISSING SERIAL IN YOUR SCREENSHOT
                "serial_for_device": getattr(d, 'serial_for_device', None), 
                # THIS HELPS FIX THE INVALID DATE
                "uploaded_at": d.uploaded_at.isoformat() + "Z" if getattr(d, 'uploaded_at', None) else None
            } for d in pending_docs
        ]
    })

@router.post("/kyc/{doc_id}/review")
def review_kyc_document(
    doc_id: str,
    payload: DocReviewPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.ADMIN]))
):
    """
    Admin accepts or rejects a KYC document.
    On ACCEPT: user is activated. This is the ONLY path to ACTIVE for
    FIRST_BUYER and RESELLER — purchase/business proof must be human-verified.
    """
    if payload.decision not in ["ACCEPT", "REJECT"]:
        raise HTTPException(status_code=400, detail="Decision must be ACCEPT or REJECT.")

    doc = db.query(KYCDocument).filter(KYCDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    if doc.status != DocStatus.PENDING:
        raise HTTPException(status_code=400, detail=f"Document already reviewed: {doc.status}.")

    try:
        doc.reviewed_by = current_user.id
        doc.reviewed_at = datetime.utcnow()

        user = db.query(User).filter(User.id == doc.user_id).first()

        if payload.decision == "ACCEPT":
            doc.status = DocStatus.ACCEPTED

            # Activate the user — only after human admin verified the document
            user.status = UserStatus.ACTIVE
            user.admin_approved = True
            user.admin_approved_by = current_user.id
            user.admin_approved_at = datetime.utcnow()
            message = "Document accepted. User account is now ACTIVE."

        else:  # REJECT
            if not payload.rejection_reason:
                raise HTTPException(status_code=400, detail="rejection_reason required when rejecting.")
            doc.status = DocStatus.REJECTED
            doc.rejection_reason = payload.rejection_reason
            user.status = UserStatus.REJECTED
            message = "Document rejected. User account has been rejected."

        audit_log = AdminAction(
            admin_id=current_user.id,
            action_type=AdminActionType.DOC_REVIEW,
            target_user_id=doc.user_id,
            notes=f"{payload.decision}: {payload.rejection_reason or 'approved'}"
        )
        db.add(audit_log)
        db.commit()

        return standard_response({
            "doc_id": doc.id,
            "decision": payload.decision,
            "user_id": doc.user_id,
            "user_status": user.status,
            "message": message
        })

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# --- 3. DEVICE EXPORT (regulatory override, terminal) ---

@router.post("/devices/{device_id}/export")
def export_device(
    device_id: str,
    payload: ExportDevicePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.ADMIN]))
):
    """
    Regulatory override — marks device as EXPORTED (terminal state).
    Admin only. Works from any non-terminal state per SOP Rule 8.
    """
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found.")

    if device.status in [DeviceStatus.RECYCLED, DeviceStatus.EXPORTED]:
        raise HTTPException(
            status_code=409,
            detail=f"Device is already in terminal state: {device.status}."
        )

    try:
        chain_result = ledger_service.mark_exported(device_id=device_id)

        device.status = DeviceStatus.EXPORTED
        device.stamp_valid = False

        audit_log = AdminAction(
            admin_id=current_user.id,
            action_type=AdminActionType.REVOKE,
            target_device_id=device_id,
            notes=payload.notes
        )
        db.add(audit_log)
        db.commit()

        return standard_response({
            "device_id": device_id,
            "status": "EXPORTED",
            "chain_tx_id": chain_result["tx_id"],
            "message": "Device permanently exported on-chain."
        })

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Export failed: {str(e)}")


@router.get("/devices")
def get_all_devices(
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.ADMIN]))
):
    """Admin oversight — all devices across all states."""
    devices = db.query(Device).order_by(Device.registered_at.desc()).all()
    return standard_response({
        "total": len(devices),
        "devices": [
            {
                "device_id": d.id,
                "brand": d.brand_name,
                "status": d.status,
                "stamp_valid": d.stamp_valid,
                "current_owner_id": d.current_owner_id,
                "is_for_sale": d.is_for_sale,
                "registered_at": d.registered_at.isoformat() + "Z" if d.registered_at else None
            } for d in devices
        ]
    })