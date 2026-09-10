import hashlib
import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import Any

# Import frozen models, RBAC, and Ledger Service
from database import get_db, Device, DeviceHardwareLog, ChangeType, User, DeviceStatus
from auth import get_current_user
from services.ledger_service import ledger_service

router = APIRouter(prefix="/device-profiles", tags=["Device Profiles"])

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
    """Enforces SHA-256 hashing for on-chain integrity."""
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


# --- PAYLOAD SCHEMAS ---
class HardwareChangePayload(BaseModel):
    component: str = Field(..., description="E.g., RAM, Storage, Battery")
    change_type: ChangeType
    old_spec: str = Field(None, description="Previous spec (if known)")
    new_spec: str = Field(..., description="The newly installed spec")


# --- ROUTES ---

@router.post("/{device_id}/hardware-change", status_code=status.HTTP_200_OK)
def declare_hardware_change(
    device_id: str,
    payload: HardwareChangePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Logs a hardware upgrade/replacement.
    Invalidates current stamp and updates the Algorand blockchain.
    """
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found.")

    # 1. Authorization: Only the current owner can declare a change
    if device.current_owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Only the current device owner can declare hardware changes."
        )

    # 2. Hard Block: Cannot alter a disputed device (SOP Rule 6)
    if device.status == DeviceStatus.DISPUTED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, 
            detail="Hardware changes are blocked while the device is in a DISPUTED state."
        )

    # 3. Apply the hardware change to the current_config dictionary
    updated_config = device.current_config.copy()
    
    # Case-insensitive key replacement (e.g., matching "ram" to "RAM")
    existing_key = next((k for k in updated_config.keys() if k.lower() == payload.component.lower()), None)
    if existing_key:
        updated_config[existing_key] = payload.new_spec
    else:
        updated_config[payload.component] = payload.new_spec

    # 4. Hash the new configuration for the blockchain
    config_str = json.dumps(updated_config, sort_keys=True)
    new_config_hash = generate_sha256(config_str)

    try:
        # 5. Synchronous Blockchain Mutation (Pillar 1)
        # Invalidates the stamp on-chain via PuyaPy contract
        chain_result = ledger_service.declare_hardware_change(
            device_id=device.id,
            new_config_hash=new_config_hash
        )

        # 6. Update PostgreSQL Device Profile (Pillar 3)
        device.current_config = updated_config
        device.stamp_valid = False  # Explicitly invalidate the stamp[cite: 4, 7]
        
        # 7. Append to the Immutable Hardware Log (Pillar 3)
        log_entry = DeviceHardwareLog(
            device_id=device.id,
            component=payload.component,
            change_type=payload.change_type,
            old_spec=payload.old_spec,
            new_spec=payload.new_spec,
            declared_by=current_user.id,
            stamp_invalidated=True
        )
        db.add(log_entry)
        db.commit()

        return standard_response({
            "device_id": device.id,
            "chain_tx_id": chain_result["tx_id"],
            "stamp_valid": False,
            "message": "Hardware change recorded on-chain. Re-verification required before next transfer."
        })

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Ledger hardware update failed: {str(e)}"
        )


@router.get("/{device_id}/hardware-log")
def get_hardware_log(
    device_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Fetches the append-only hardware change log for a device[cite: 7]."""
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found.")

    logs = db.query(DeviceHardwareLog).filter(
        DeviceHardwareLog.device_id == device_id
    ).order_by(DeviceHardwareLog.created_at.desc()).all()

    return standard_response({
        "device_id": device_id,
        "current_config": device.current_config,
        "hardware_log": [
            {
                "log_id": log.id,
                "component": log.component,
                "change_type": log.change_type,
                "old_spec": log.old_spec,
                "new_spec": log.new_spec,
                "declared_at": log.created_at.isoformat() + "Z"
            } for log in logs
        ]
    })
