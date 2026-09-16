import hashlib
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

# Consolidated Import: models, RBAC, and Ledger Service
from database import (
    get_db, Device, DeviceStatus, User, UserRole, 
    Dispute, DisputeStatus, DisputeType, Transfer, TransferStatus
)
from auth import get_current_user, RequireRole
from services.ledger_service import ledger_service

router = APIRouter(prefix="/disputes", tags=["Disputes"])

def standard_response(data: dict = None, code: int = 200) -> dict:
    """Mandatory API response envelope per SOP Section 4.1."""
    return {
        "status": "success",
        "code": code,
        "data": data or {},
        "error": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "traceloop_version": "v1"
    }

# --- PAYLOAD SCHEMAS ---
class RaiseDisputePayload(BaseModel):
    device_id: str
    dispute_type: DisputeType
    description: str = Field(..., min_length=10)
    evidence_path: str = None

class ResolveDisputePayload(BaseModel):
    resolved_state: DeviceStatus
    resolution_note: str = Field(..., min_length=10)
    revert_ownership: bool = False  # NEW: Admin decides if ownership returns to seller

# Enum to Integer mapping for Algorand smart contract (ARC-56)
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


# --- ROUTES ---

@router.post("/raise", status_code=status.HTTP_201_CREATED)
def raise_dispute(
    payload: RaiseDisputePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Step 1: Any user flags a device as stolen or fake.
    Step 2: Backend (Operator) pushes the dispute to Algorand.
    Step 3: Device stamp is instantly invalidated and pulled from the marketplace.
    """
    device = db.query(Device).filter(Device.id == payload.device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found.")

    if device.status in [DeviceStatus.RECYCLED, DeviceStatus.EXPORTED]:
        raise HTTPException(status_code=400, detail="Cannot dispute a terminal device.")

    try:
        # 1. Web3 Synchronous Call
        dispute_int = DISPUTE_TYPE_MAP.get(payload.dispute_type, 5)
        chain_result = ledger_service.raise_dispute(
            device_id=device.id, 
            dispute_type=dispute_int,
            raised_by_role="OPERATOR"
        )

        # 2. Web2 Database Updates
        device.status = DeviceStatus.DISPUTED
        device.stamp_valid = False  
        device.is_for_sale = False  
        
        new_dispute = Dispute(
            device_id=device.id,
            raised_by=current_user.id,
            raised_by_role=current_user.role,
            dispute_type=payload.dispute_type,
            description=payload.description,
            evidence_path=payload.evidence_path,
            status=DisputeStatus.OPEN
        )
        db.add(new_dispute)
        db.commit()
        db.refresh(new_dispute)

        return standard_response({
            "dispute_id": new_dispute.id,
            "device_id": device.id,
            "chain_tx_id": chain_result["tx_id"],
            "message": "Dispute raised successfully. Device locked on-chain and removed from marketplace."
        }, 201)

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to raise dispute: {str(e)}")


@router.patch("/{dispute_id}/resolve")
def resolve_dispute(
    dispute_id: str,
    payload: ResolveDisputePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.ADMIN]))
):
    """
    Step 1: Admin reviews the dispute.
    Step 2: Admin restores the device to a valid state on Algorand.
    Strictly enforces the rule that a dispute cannot resolve into a terminal state.
    """
    dispute = db.query(Dispute).filter(Dispute.id == dispute_id).first()
    if not dispute or dispute.status != DisputeStatus.OPEN:
        raise HTTPException(status_code=404, detail="Open dispute not found.")

    device = db.query(Device).filter(Device.id == dispute.device_id).first()

    if payload.resolved_state not in [DeviceStatus.REGISTERED, DeviceStatus.VERIFIED, DeviceStatus.TRANSFERRED]:
        raise HTTPException(
            status_code=400, 
            detail="Disputes can only resolve into REGISTERED, VERIFIED, or TRANSFERRED."
        )

    try:
        state_int = STATE_MAP[payload.resolved_state]
        
        # 1. Web3 Synchronous Call
        chain_result = ledger_service.resolve_dispute(
            device_id=device.id,
            resolved_state=state_int
        )

        # 2. Web2 Database Updates
        device.status = payload.resolved_state
        device.stamp_valid = False 
        
        # If deal cancelled — return ownership to seller
        if payload.revert_ownership:
            last_transfer = db.query(Transfer).filter(
                Transfer.device_id == device.id,
                Transfer.status == TransferStatus.COMPLETED
            ).order_by(Transfer.completed_at.desc()).first()
            
            if last_transfer:
                prev_owner_hash = hashlib.sha256(last_transfer.from_user_id.encode('utf-8')).hexdigest()
                device.current_owner_id = last_transfer.from_user_id
                device.current_owner_hash = prev_owner_hash
        
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
            "message": "Dispute resolved. Device requires re-verification before next transfer."
        })

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to resolve dispute: {str(e)}")


@router.get("/device/{device_id}")
def get_device_disputes(
    device_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Fetches all disputes attached to a specific device."""
    disputes = db.query(Dispute).filter(Dispute.device_id == device_id).all()
    
    return standard_response({
        "device_id": device_id,
        "disputes": [
            {
                "dispute_id": d.id,
                "type": d.dispute_type,
                "status": d.status,
                "raised_at": d.created_at.isoformat(),
                "resolved_at": d.resolved_at.isoformat() if d.resolved_at else None
            } for d in disputes
        ]
    })


@router.get("/my")
def get_my_disputes(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Any authenticated user sees disputes they raised."""
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

    return standard_response({
        "total": len(results),
        "my_disputes": results
    })


@router.get("/open")
def get_open_disputes(
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.ADMIN]))
):
    """
    Admin dispute queue — all open disputes across all devices.
    This is the entry point for admin resolution workflow.
    """
    disputes = db.query(Dispute).filter(
        Dispute.status.in_([DisputeStatus.OPEN, DisputeStatus.UNDER_REVIEW])
    ).order_by(Dispute.created_at.desc()).all()

    results = []
    for d in disputes:
        device = db.query(Device).filter(Device.id == d.device_id).first()
        results.append({
            "dispute_id": d.id,
            "device_id": d.device_id,
            "brand_name": device.brand_name if device else "Unknown",
            "current_config": device.current_config if device else {},
            "dispute_type": d.dispute_type,
            "description": d.description,
            "evidence_path": d.evidence_path,
            "raised_by": d.raised_by,
            "raised_by_role": d.raised_by_role,
            "status": d.status,
            "created_at": d.created_at.isoformat() if d.created_at else None # FIX: Removed + "Z"
        })

    return standard_response({
        "total_open": len(results),
        "disputes": results
    })