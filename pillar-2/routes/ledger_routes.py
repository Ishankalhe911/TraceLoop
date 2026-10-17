from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db, Device, LedgerEvent, User
from auth import get_current_user
from services.ledger_service import ledger_service

router = APIRouter(prefix="/ledger", tags=["Blockchain Ledger"])

def standard_response(data: dict = None, code: int = 200) -> dict:
    return {
        "status": "success", "code": code, "data": data or {},
        "error": None, "timestamp": datetime.utcnow().isoformat() + "Z", "traceloop_version": "v1"
    }

@router.get("/device/{device_id}/state")
def get_onchain_state(
    device_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Read the on-chain state by the public Passport ID."""
    device = db.query(Device).filter(Device.public_id == device_id).first()
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found.")
    try:
        chain_state = ledger_service.get_device_state(device.public_id)
        return standard_response(chain_state)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read from Algorand: {str(e)}")

@router.get("/device/{device_id}/events")
def get_offchain_ledger_events(
    device_id: str, 
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Fetches the fast, off-chain mirror of blockchain events.
    Populated asynchronously by your Subscriber Daemon[cite: 7].
    """
    device = db.query(Device).filter(Device.public_id == device_id).first()
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found.")

    events = (
        db.query(LedgerEvent)
        .filter(LedgerEvent.device_id == device.id)
        .order_by(LedgerEvent.timestamp.desc())
        .all()
    )

    if not events:
        return standard_response({"device_id": device.public_id, "events": []})
        
    history = [{
        "event_id": e.id,
        "event_type": e.event_type,
        "actor_role": e.actor_type,
        "tx_hash": e.tx_hash,
        "block_number": e.block_number,
        "timestamp": e.timestamp.isoformat() + "Z"
    } for e in events]
    
    return standard_response({"device_id": device.public_id, "events": history})