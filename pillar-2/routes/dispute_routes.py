"""
dispute_routes.py — Trace-Loop Pillar 2
Report → Admin Review → On-Chain Freeze model.

Changes from previous version:
  - POST /disputes/report      : Web2 only. Any user files a report. No chain call. No freeze.
  - POST /disputes/{id}/freeze : Admin only. Triggers on-chain freeze after review.
  - PATCH /disputes/{id}/resolve: Admin only. Unchanged except revert_ownership REMOVED.
  - Duplicate active dispute check added.
  - DisputeStatus.UNDER_REVIEW used as initial state for reports.
"""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from database import (
    get_db, Device, DeviceStatus, User, UserRole,
    Dispute, DisputeStatus, DisputeType
)
from auth import get_current_user, RequireRole
from services.ledger_service import ledger_service

router = APIRouter(prefix="/disputes", tags=["Disputes"])


def standard_response(data: dict = None, code: int = 200) -> dict:
    return {
        "status": "success",
        "code": code,
        "data": data or {},
        "error": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "traceloop_version": "v1"
    }


# --- PAYLOAD SCHEMAS ---

class ReportDisputePayload(BaseModel):
    device_id: str
    dispute_type: DisputeType
    description: str = Field(..., min_length=10)
    evidence_path: str = None


class ResolveDisputePayload(BaseModel):
    resolved_state: DeviceStatus
    resolution_note: str = Field(..., min_length=10)
    # revert_ownership REMOVED — creates DB/chain ownership mismatch.
    # On-chain resolution does not perform ownership rollback.
    # If ownership needs to change, use the transfer flow after resolution.


# Enum → Integer for Algorand ARC-56 contract
DISPUTE_TYPE_MAP = {
    DisputeType.MISREPRESENTED_SPEC: 1,
    DisputeType.STOLEN: 2,
    DisputeType.FAKE_STAMP: 3,
    DisputeType.OWNERSHIP_DISPUTE: 4,
    DisputeType.OTHER: 5
}

STATE_MAP = {
    DeviceStatus.REGISTERED: 1,
    DeviceStatus.VERIFIED: 2,
    DeviceStatus.TRANSFERRED: 3
}


# ─────────────────────────────────────────────
# STEP 1 — REPORT (Web2 only, any active user)
# ─────────────────────────────────────────────

@router.post("/report", status_code=status.HTTP_201_CREATED)
def report_dispute(
    payload: ReportDisputePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Any authenticated user submits a dispute report.
    Web2 only — no chain call, no device freeze.
    Dispute goes to UNDER_REVIEW for admin to assess.
    Admin then calls POST /disputes/{id}/freeze to trigger on-chain action.
    """
    device = db.query(Device).filter(Device.id == payload.device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found.")

    # Block reports on terminal devices
    if device.status in [DeviceStatus.RECYCLED, DeviceStatus.EXPORTED]:
        raise HTTPException(
            status_code=400,
            detail="Cannot report a terminal device (RECYCLED or EXPORTED)."
        )

    # Duplicate active dispute guard
    # If already DISPUTED on-chain, a second chain call would revert anyway.
    # Block at Web2 level before it gets there.
    if device.status == DeviceStatus.DISPUTED:
        raise HTTPException(
            status_code=409,
            detail="Device is already in DISPUTED state. "
                   "Resolve the existing dispute before filing a new report."
        )

    # Check no OPEN or UNDER_REVIEW dispute already exists for this device
    existing = db.query(Dispute).filter(
        Dispute.device_id == payload.device_id,
        Dispute.status.in_([DisputeStatus.OPEN, DisputeStatus.UNDER_REVIEW])
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"An active dispute report already exists for this device "
                   f"(dispute_id: {existing.id}). Await admin review."
        )

    try:
        new_dispute = Dispute(
            device_id=device.id,
            raised_by=current_user.id,
            raised_by_role=current_user.role,
            dispute_type=payload.dispute_type,
            description=payload.description,
            evidence_path=payload.evidence_path,
            status=DisputeStatus.UNDER_REVIEW   # Starts as UNDER_REVIEW, not OPEN
        )
        db.add(new_dispute)
        db.commit()
        db.refresh(new_dispute)

        return standard_response({
            "dispute_id": new_dispute.id,
            "device_id": device.id,
            "status": DisputeStatus.UNDER_REVIEW,
            "message": "Report submitted. An admin will review and freeze the device "
                       "on-chain if the dispute is valid. Device remains active until then."
        }, 201)

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to submit report: {str(e)}")


# ─────────────────────────────────────────────
# STEP 2 — FREEZE (Admin only, triggers chain)
# ─────────────────────────────────────────────

@router.post("/{dispute_id}/freeze", status_code=status.HTTP_200_OK)
def freeze_device(
    dispute_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.ADMIN]))
):
    """
    Admin reviews the report and approves the on-chain freeze.
    This is the ONLY point where raise_dispute() is called on Algorand.
    Security: Admin is the gate. No user can trigger a chain freeze directly.
    """
    dispute = db.query(Dispute).filter(Dispute.id == dispute_id).first()
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found.")

    if dispute.status != DisputeStatus.UNDER_REVIEW:
        raise HTTPException(
            status_code=409,
            detail=f"Dispute cannot be frozen. Current status: {dispute.status}. "
                   "Only UNDER_REVIEW disputes can be escalated to on-chain freeze."
        )

    device = db.query(Device).filter(Device.id == dispute.device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found.")

    # Re-check device isn't already frozen (race condition guard)
    if device.status == DeviceStatus.DISPUTED:
        raise HTTPException(
            status_code=409,
            detail="Device is already in DISPUTED state on-chain."
        )

    if device.status in [DeviceStatus.RECYCLED, DeviceStatus.EXPORTED]:
        raise HTTPException(
            status_code=400,
            detail="Device reached a terminal state before freeze could be applied."
        )

    try:
        # On-chain freeze — Pillar 1
        dispute_int = DISPUTE_TYPE_MAP.get(dispute.dispute_type, 5)
        chain_result = ledger_service.raise_dispute(
            device_id=device.id,
            dispute_type=dispute_int,
            raised_by_role="OPERATOR"
        )

        # Web2 state updates — Pillar 3
        device.status = DeviceStatus.DISPUTED
        device.stamp_valid = False
        device.is_for_sale = False

        # Dispute moves from UNDER_REVIEW → OPEN (now active on-chain)
        dispute.status = DisputeStatus.OPEN

        db.commit()

        return standard_response({
            "dispute_id": dispute.id,
            "device_id": device.id,
            "chain_tx_id": chain_result["tx_id"],
            "confirmed_round": chain_result.get("confirmed_round"),
            "message": "Device frozen on Algorand. Stamp invalidated. "
                       "Removed from marketplace. Dispute is now OPEN."
        })

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"On-chain freeze failed: {str(e)}"
        )


# ─────────────────────────────────────────────
# STEP 3 — RESOLVE (Admin only, restores chain state)
# ─────────────────────────────────────────────

@router.patch("/{dispute_id}/resolve")
def resolve_dispute(
    dispute_id: str,
    payload: ResolveDisputePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.ADMIN]))
):
    """
    Admin resolves a frozen (OPEN) dispute.
    Restores device to REGISTERED, VERIFIED, or TRANSFERRED on-chain.
    stamp_valid is always False after resolution — re-verification required.

    NOTE: Ownership is NOT reverted here.
    The on-chain contract does not perform ownership rollback on resolve_dispute().
    If ownership needs to change, execute a new transfer after resolution.
    """
    dispute = db.query(Dispute).filter(Dispute.id == dispute_id).first()
    if not dispute or dispute.status != DisputeStatus.OPEN:
        raise HTTPException(
            status_code=404,
            detail="Open dispute not found. Only OPEN disputes can be resolved."
        )

    device = db.query(Device).filter(Device.id == dispute.device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found.")

    # Enforce contract rule: cannot resolve into terminal or disputed states
    if payload.resolved_state not in [
        DeviceStatus.REGISTERED,
        DeviceStatus.VERIFIED,
        DeviceStatus.TRANSFERRED
    ]:
        raise HTTPException(
            status_code=400,
            detail="Disputes can only resolve into REGISTERED, VERIFIED, or TRANSFERRED. "
                   "Use mark_recycled or mark_exported for terminal states."
        )

    try:
        state_int = STATE_MAP[payload.resolved_state]

        # On-chain resolution — Pillar 1
        chain_result = ledger_service.resolve_dispute(
            device_id=device.id,
            resolved_state=state_int
        )

        # Web2 state updates — Pillar 3
        # stamp_valid ALWAYS False after resolution — enforced by contract too
        device.status = payload.resolved_state
        device.stamp_valid = False

        dispute.status = DisputeStatus.RESOLVED
        dispute.resolution_note = payload.resolution_note
        dispute.resolved_by = current_user.id
        dispute.resolved_at = datetime.now(timezone.utc)

        db.commit()

        return standard_response({
            "dispute_id": dispute.id,
            "device_id": device.id,
            "new_device_state": payload.resolved_state,
            "stamp_valid": False,
            "chain_tx_id": chain_result["tx_id"],
            "confirmed_round": chain_result.get("confirmed_round"),
            "message": "Dispute resolved on Algorand. "
                       "Device requires re-verification before next transfer."
        })

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to resolve dispute: {str(e)}"
        )


# ─────────────────────────────────────────────
# READ ROUTES
# ─────────────────────────────────────────────

@router.get("/device/{device_id}")
def get_device_disputes(
    device_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Full dispute history for a device — permanently attached, never deleted."""
    disputes = db.query(Dispute).filter(
        Dispute.device_id == device_id
    ).order_by(Dispute.created_at.desc()).all()

    return standard_response({
        "device_id": device_id,
        "disputes": [
            {
                "dispute_id": d.id,
                "type": d.dispute_type,
                "status": d.status,
                "description": d.description,
                "raised_by_role": d.raised_by_role,
                "raised_at": d.created_at.isoformat() if d.created_at else None,
                "resolved_at": d.resolved_at.isoformat() if d.resolved_at else None,
                "resolution_note": d.resolution_note
            } for d in disputes
        ]
    })


@router.get("/my")
def get_my_disputes(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Reports filed by the current user."""
    disputes = db.query(Dispute).filter(
        Dispute.raised_by == current_user.id
    ).order_by(Dispute.created_at.desc()).all()

    results = []
    for d in disputes:
        device = db.query(Device).filter(Device.id == d.device_id).first()
        results.append({
            "dispute_id": d.id,
            "device_id": d.device_id,
            "brand_name": device.brand_name if device else "Unknown",
            "dispute_type": d.dispute_type,
            "description": d.description,
            "status": d.status,
            "resolution_note": d.resolution_note,
            "created_at": d.created_at.isoformat() if d.created_at else None,
            "resolved_at": d.resolved_at.isoformat() if d.resolved_at else None
        })

    return standard_response({"total": len(results), "my_disputes": results})


@router.get("/open")
def get_open_disputes(
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.ADMIN]))
):
    """
    Admin queue — all UNDER_REVIEW and OPEN disputes.
    UNDER_REVIEW = reported, awaiting admin freeze decision.
    OPEN = frozen on-chain, awaiting admin resolution.
    """
    disputes = db.query(Dispute).filter(
        Dispute.status.in_([DisputeStatus.UNDER_REVIEW, DisputeStatus.OPEN])
    ).order_by(Dispute.created_at.desc()).all()

    results = []
    for d in disputes:
        device = db.query(Device).filter(Device.id == d.device_id).first()
        results.append({
            "dispute_id": d.id,
            "device_id": d.device_id,
            "brand_name": device.brand_name if device else "Unknown",
            "current_device_status": device.status if device else "UNKNOWN",
            "current_config": device.current_config if device else {},
            "dispute_type": d.dispute_type,
            "description": d.description,
            "evidence_path": d.evidence_path,
            "raised_by": d.raised_by,
            "raised_by_role": d.raised_by_role,
            "status": d.status,
            "next_action": (
                "POST /disputes/{id}/freeze — approve and freeze on-chain"
                if d.status == DisputeStatus.UNDER_REVIEW
                else "PATCH /disputes/{id}/resolve — resolve and restore on-chain"
            ),
            "created_at": d.created_at.isoformat() if d.created_at else None
        })

    return standard_response({
        "total_pending": len(results),
        "disputes": results
    })
