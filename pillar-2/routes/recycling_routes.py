import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel

# Import frozen models, RBAC, and Ledger Service
from database import (
    get_db, Device, DeviceStatus, User, UserRole, 
    RecyclingRecord, RecyclingStatus
)
from auth import RequireRole, get_current_user
from services.ledger_service import ledger_service

router = APIRouter(prefix="/recycling", tags=["Recycling"])

def standard_response(data: dict = None, code: int = 200) -> dict:
    """Mandatory API response envelope per SOP Section 4.1."""
    return {
        "status": "success",
        "code": code,
        "data": data or {},
        "error": None,
        # FIX: Timezone-aware timestamp
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "traceloop_version": "v1"
    }

# --- PAYLOAD SCHEMAS ---
class ReceiveDevicePayload(BaseModel):
    device_id: str
    condition_notes: str = None

class CompleteRecyclingPayload(BaseModel):
    device_id: str

# --- ROUTES ---

@router.post("/receive", status_code=status.HTTP_200_OK)
def receive_device(
    payload: ReceiveDevicePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.RECYCLER]))
):
    """
    Step 1: Recycler acknowledges physical receipt of the device.
    Purely off-chain logistical tracking. Does NOT mutate the blockchain.
    """
    device = db.query(Device).filter(Device.id == payload.device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found.")

    # Security Check: Device must have been legally transferred to this specific recycler
    if device.current_owner_id != current_user.id or device.status != DeviceStatus.TRANSFERRED:
        raise HTTPException(
            status_code=403, 
            detail="Device has not been officially transferred to your facility via the verification network."
        )

    try:
        record = db.query(RecyclingRecord).filter(
            RecyclingRecord.device_id == device.id,
            RecyclingRecord.recycler_id == current_user.id
        ).first()

        now_utc = datetime.now(timezone.utc)

        if not record:
            record = RecyclingRecord(
                device_id=device.id,
                recycler_id=current_user.id,
                recycling_status=RecyclingStatus.RECEIVED,
                received_date=now_utc  # FIX: Timezone aware
            )
            db.add(record)
        else:
            record.recycling_status = RecyclingStatus.RECEIVED
            record.received_date = now_utc

        db.commit()

        return standard_response({
            "device_id": device.id,
            "logistical_status": record.recycling_status,
            "message": "Physical receipt logged. Ready for recycling processing."
        })

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/complete", status_code=status.HTTP_200_OK)
def complete_recycling(
    payload: CompleteRecyclingPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.RECYCLER]))
):
    """
    Step 2: Recycler permanently terminates the device.
    Executes a synchronous call to Algorand to burn the device state to RECYCLED.
    Generates the CPCB-compliant destruction certificate.
    """
    device = db.query(Device).filter(Device.id == payload.device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found.")

    # Hard Block: SOP requires the device to be freshly TRANSFERRED
    if device.status != DeviceStatus.TRANSFERRED:
        raise HTTPException(
            status_code=409, 
            detail=f"Device must be in TRANSFERRED state to be recycled. Current state: {device.status}"
        )

    record = db.query(RecyclingRecord).filter(
        RecyclingRecord.device_id == device.id,
        RecyclingRecord.recycler_id == current_user.id
    ).first()

    if not record or record.recycling_status == RecyclingStatus.RECYCLED:
        raise HTTPException(status_code=400, detail="Invalid recycling record state or already recycled.")

    try:
        # FIX #6: UUID-based certificate ID — eliminates race condition from count-based approach
        certificate_id = f"TRC-CERT-{uuid.uuid4().hex[:8].upper()}"

        # Pre-flight: confirm TRANSFERRED state on-chain
        if not ledger_service.can_recycle(device.id):
            raise HTTPException(
                status_code=409,
                detail="Chain pre-check failed: device must be in TRANSFERRED state on-chain to be recycled."
            )

        # Synchronous Blockchain Mutation (Pillar 1)
        chain_result = ledger_service.mark_recycled(device_id=device.id)

        # Web2 Database Updates (Terminal State)
        device.status = DeviceStatus.RECYCLED
        device.stamp_valid = False
        device.is_for_sale = False  # Guarantee it is pulled from listings
        
        record.recycling_status = RecyclingStatus.RECYCLED
        record.certificate_id = certificate_id
        record.completion_date = datetime.now(timezone.utc)  # FIX: Timezone aware

        db.commit()

        return standard_response({
            "device_id": device.id,
            "status": "RECYCLED",
            "certificate_id": certificate_id,
            "chain_tx_id": chain_result["tx_id"],
            "message": "Device successfully terminated on the blockchain and E-Waste certificate generated."
        })

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Ledger termination failed: {str(e)}"
        )