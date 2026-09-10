import hashlib
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List

# Import frozen models and security mechanisms
from database import get_db, Device, Transfer, User, UserRole, DeviceStatus, TransferStatus
from auth import get_current_user, RequireRole
from services.ledger_service import ledger_service

router = APIRouter(prefix="/transfers", tags=["Transfers"])

def standard_response(data: dict = None, code: int = 200) -> dict:
    """Mandatory API response envelope per SOP Section 4.1."""
    return {
        "status": "success",
        "code": code,
        "data": data or {},
        "error": None,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "traceloop_version": "v1"
    }

def generate_sha256(data: str) -> str:
    """Enforces SHA-256 hashing for on-chain privacy per SOP Section 3.3."""
    return hashlib.sha256(data.encode('utf-8')).hexdigest()

# --- PAYLOAD SCHEMAS ---
class InitiateTransferPayload(BaseModel):
    device_id: str
    to_user_id: str

# --- ROUTES ---

@router.post("/initiate", status_code=status.HTTP_201_CREATED)
def initiate_transfer(
    payload: InitiateTransferPayload, 
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.FIRST_BUYER, UserRole.BUYER, UserRole.RESELLER]))
):
    """
    Step 1: Owner initiates a transfer to a new user. 
    Enforces the rule that transfers must wait for a VERIFIABLE node to complete.
    """
    device = db.query(Device).filter(Device.id == payload.device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
        
    if device.current_owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the current owner can initiate a transfer.")
        
    # Hard Block: Stamp Validity Check (SOP Section 4.2 & 5.1)
    if not device.stamp_valid:
        raise HTTPException(
            status_code=409, 
            detail="Transfer blocked: Device verification stamp is invalid. Must be re-verified first."
        )

    # Hard Block: Cannot transfer to self
    if payload.to_user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot transfer device to yourself.")
        
    receiver = db.query(User).filter(User.id == payload.to_user_id).first()
    if not receiver or receiver.status != "ACTIVE":
        raise HTTPException(status_code=404, detail="Receiver not found or account is not active.")

    # --- FRAUD SIGNAL CHECKS (SOP Section 5.3)[cite: 15] ---
    velocity_flagged = False
    count_flagged = False
    
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    
    # 1. Velocity Check: >2 transfers out in 30 days (BUYER only)
    if current_user.role == UserRole.BUYER:
        recent_transfers = db.query(Transfer).filter(
            Transfer.from_user_id == current_user.id,
            Transfer.initiated_at >= thirty_days_ago
        ).count()
        if recent_transfers >= 2:
            velocity_flagged = True
            
    # 2. Device Count Check: Receiver holding >3 active devices (BUYER only)
    if receiver.role == UserRole.BUYER:
        active_devices = db.query(Device).filter(
            Device.current_owner_id == receiver.id,
            Device.status.in_([DeviceStatus.REGISTERED, DeviceStatus.VERIFIED, DeviceStatus.TRANSFERRED])
        ).count()
        if active_devices >= 3:
            count_flagged = True

    try:
        # Create Transfer Record
        new_transfer = Transfer(
            device_id=payload.device_id,
            from_user_id=current_user.id,
            to_user_id=payload.to_user_id,
            status=TransferStatus.PENDING,
            velocity_flagged=velocity_flagged,
            count_flagged=count_flagged
        )
        db.add(new_transfer)
        db.commit()
        db.refresh(new_transfer)
        
        return standard_response({
            "transfer_id": new_transfer.id,
            "device_id": new_transfer.device_id,
            "status": new_transfer.status,
            "message": "Transfer initiated. Pending VERIFIABLE node completion."
        }, 201)
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{transfer_id}/complete")
def complete_transfer(
    transfer_id: str, 
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.VERIFIABLE]))
):
    """
    Step 2: Verifiable node executes the ownership change on the blockchain.
    Enforces the protocol block against direct buyer-to-buyer transfers[cite: 15].
    """
    transfer = db.query(Transfer).filter(Transfer.id == transfer_id).first()
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer record not found")
        
    if transfer.status != TransferStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"Transfer is already {transfer.status}")
        
    device = db.query(Device).filter(Device.id == transfer.device_id).first()
    
    # Hash the new owner's ID for the blockchain to protect PII[cite: 15]
    new_owner_hash = generate_sha256(transfer.to_user_id)

    try:
        # 1. Execute synchronous blockchain mutation via LedgerService[cite: 15]
        chain_result = ledger_service.transfer_device(
            device_id=device.id,
            new_owner_hash=new_owner_hash
        )
        
        # 2. Update off-chain PostgreSQL State
        device.current_owner_id = transfer.to_user_id
        device.current_owner_hash = new_owner_hash
        device.status = DeviceStatus.TRANSFERRED
        
        transfer.status = TransferStatus.COMPLETED
        transfer.verifier_id = current_user.id
        transfer.completed_at = datetime.utcnow()
        
        db.commit()
        
        return standard_response({
            "transfer_id": transfer.id,
            "device_id": device.id,
            "new_owner_id": transfer.to_user_id,
            "chain_tx_id": chain_result["tx_id"],
            "status": "COMPLETED"
        })
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ledger or DB execution failed: {str(e)}")


@router.get("/device/{device_id}")
def get_device_transfer_history(
    device_id: str, 
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Fetches full transfer history for a device. Allowed for any active role[cite: 15]."""
    transfers = db.query(Transfer).filter(Transfer.device_id == device_id).order_by(Transfer.initiated_at.desc()).all()
    
    history = []
    for t in transfers:
        history.append({
            "transfer_id": t.id,
            "from_user_id": t.from_user_id,
            "to_user_id": t.to_user_id,
            "verifier_id": t.verifier_id,
            "status": t.status,
            "initiated_at": t.initiated_at.isoformat() + "Z" if t.initiated_at else None,
            "completed_at": t.completed_at.isoformat() + "Z" if t.completed_at else None
        })
        
    return standard_response({
        "device_id": device_id,
        "history": history
    })
