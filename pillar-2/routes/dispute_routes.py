from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

# Import frozen models, RBAC, and Ledger Service
from database import (
    get_db, Device, DeviceStatus, User, UserRole, 
    Dispute, DisputeStatus, DisputeType
)
from auth import get_current_user, RequireRole
from services.ledger_service import ledger_service

router = APIRouter(prefix="/disputes", tags=["Disputes"])

def standard_response(data: dict = None, code: int = 200) -> dict:
    """Mandatory API response envelope per SOP Section 4.1[cite: 7]."""
    return {
        "status": "success",
        "code": code,
        "data": data or {},
        "error": None,
        "timestamp": datetime.utcnow().isoformat() + "Z",
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
    Step 3: Device stamp is instantly invalidated.
    """
    device = db.query(Device).filter(Device.id == payload.device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found.")

    if device.status in [DeviceStatus.RECYCLED, DeviceStatus.EXPORTED]:
        raise HTTPException(status_code=400, detail="Cannot dispute a terminal device.")

    try:
        # 1. Web3 Synchronous Call
        # Operator wallet submits the dispute to the blockchain on behalf of the user[cite: 4, 6]
        dispute_int = DISPUTE_TYPE_MAP.get(payload.dispute_type, 5)
        chain_result = ledger_service.raise_dispute(
            device_id=device.id, 
            dispute_type=dispute_int,
            raised_by_role="OPERATOR"
        )

        # 2. Web2 Database Updates
        device.status = DeviceStatus.DISPUTED
        device.stamp_valid = False  # CRITICAL: Instantly locks hardware changes and transfers
        
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
            "message": "Dispute raised successfully. Device locked on-chain."
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
    Strictly enforces the rule that a dispute cannot resolve into a terminal state[cite: 4].
    """
    dispute = db.query(Dispute).filter(Dispute.id == dispute_id).first()
    if not dispute or dispute.status != DisputeStatus.OPEN:
        raise HTTPException(status_code=404, detail="Open dispute not found.")

    device = db.query(Device).filter(Device.id == dispute.device_id).first()

    # Rule 5: resolve_dispute only allows REGISTERED, VERIFIED, TRANSFERRED[cite: 4]
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
        # Rule 7: stamp_valid is ALWAYS cleared on resolution, forcing re-verification[cite: 4]
        device.stamp_valid = False 
        
        dispute.status = DisputeStatus.RESOLVED
        dispute.resolution_note = payload.resolution_note
        dispute.resolved_by = current_user.id
        dispute.resolved_at = datetime.utcnow()

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
    """Fetches all disputes attached to a specific device[cite: 7]."""
    disputes = db.query(Dispute).filter(Dispute.device_id == device_id).all()
    
    return standard_response({
        "device_id": device_id,
        "disputes": [
            {
                "dispute_id": d.id,
                "type": d.dispute_type,
                "status": d.status,
                "raised_at": d.created_at.isoformat() + "Z",
                "resolved_at": d.resolved_at.isoformat() + "Z" if d.resolved_at else None
            } for d in disputes
        ]
    })
