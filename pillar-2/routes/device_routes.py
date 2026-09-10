import hashlib
import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import Dict, Any

# Import frozen models, RBAC, and Ledger Service
from database import get_db, Device, DeviceStatus, User, UserRole
from auth import RequireRole, get_current_user
from services.ledger_service import ledger_service

router = APIRouter(prefix="/devices", tags=["Devices"])

def standard_response(data: dict = None, code: int = 200) -> dict:
    """Mandatory API response envelope per SOP Section 4.1"""
    return {
        "status": "success",
        "code": code,
        "data": data or {},
        "error": None,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "traceloop_version": "v1"
    }

def generate_sha256(data: str) -> str:
    """Enforces SHA-256 hashing for on-chain privacy."""
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


# --- PAYLOAD SCHEMAS ---
class RegisterDevicePayload(BaseModel):
    brand_code: str = Field(..., min_length=2, max_length=4, description="E.g., DL, HP, LN")
    brand_name: str = Field(..., max_length=40, description="E.g., Dell, HP, Lenovo")
    serial_raw: str = Field(..., max_length=80, description="Raw manufacturer serial number")
    original_config: Dict[str, Any] = Field(..., description="Hardware configuration JSON")


# --- ROUTES ---

@router.post("/register", status_code=status.HTTP_201_CREATED)
def register_device(
    payload: RegisterDevicePayload, 
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.FIRST_BUYER]))
):
    """
    Registers a new device into Trace-Loop.
    Writes cryptographic hashes to Algorand TestNet, then saves raw data to PostgreSQL.
    Strictly limited to FIRST_BUYER accounts.
    """
    # 1. Enforce Standard Device ID Format (SOP Section 3.1)
    brand_code_upper = payload.brand_code.upper()
    serial_upper = payload.serial_raw.upper()
    device_id = f"TL-{brand_code_upper}-{serial_upper}"

    # 2. Idempotency Check
    if db.query(Device).filter(Device.id == device_id).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, 
            detail="Device already registered in Trace-Loop."
        )

    # 3. Cryptographic Hashing for On-Chain Privacy (SOP Section 3.3)
    serial_hash = generate_sha256(serial_upper)
    owner_hash = generate_sha256(current_user.id)
    
    # Sort keys to guarantee deterministic hashing for identical JSON configs
    config_str = json.dumps(payload.original_config, sort_keys=True)
    config_hash = generate_sha256(config_str)

    try:
        # 4. Synchronous Blockchain Mutation (Pillar 1)
        # Executes ARC-4 App call to PuyaPy contract
        chain_result = ledger_service.register_device(
            device_id=device_id,
            owner_hash=owner_hash,
            config_hash=config_hash
        )

        # 5. Off-Chain Database Storage (Pillar 3)
        new_device = Device(
            id=device_id,
            serial_hash=serial_hash,
            serial_raw=serial_upper,
            brand_code=brand_code_upper,
            brand_name=payload.brand_name,
            original_config=payload.original_config,
            current_config=payload.original_config,
            current_owner_id=current_user.id,
            current_owner_hash=owner_hash,
            status=DeviceStatus.REGISTERED,
            stamp_valid=False  # Must be verified by a service center later
        )
        
        db.add(new_device)
        db.commit()
        db.refresh(new_device)

        return standard_response({
            "device_id": new_device.id,
            "status": new_device.status,
            "chain_tx_id": chain_result["tx_id"],
            "confirmed_round": chain_result["confirmed_round"]
        }, 201)

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Registration failed: {str(e)}"
        )


@router.get("/{device_id}")
def get_device(
    device_id: str, 
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Fetches the combined off-chain and on-chain state of a device.
    Accessible to any authenticated, active user.
    """
    # 1. Fetch Off-Chain Profile (Pillar 3)
    db_device = db.query(Device).filter(Device.id == device_id).first()
    if not db_device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="Device not found off-chain."
        )

    try:
        # 2. Fetch Immutable On-Chain State (Pillar 1)
        # Reads directly from Algorand Box Storage via simulate
        chain_state = ledger_service.get_device_state(device_id)
        
        return standard_response({
            "device_id": db_device.id,
            "brand": db_device.brand_name,
            "serial_raw": db_device.serial_raw,
            "db_status": db_device.status,
            "chain_status": chain_state["state_label"],
            "stamp_valid": chain_state["stamp_valid"],
            "current_config": db_device.current_config,
            "last_verified_at": db_device.last_verified_at.isoformat() + "Z" if db_device.last_verified_at else None,
            "registered_at": db_device.registered_at.isoformat() + "Z"
        })
        
    except Exception as e:
        # Graceful degradation if Algorand indexer/node is unreachable
        return standard_response({
            "device_id": db_device.id,
            "brand": db_device.brand_name,
            "db_status": db_device.status,
            "chain_status": "UNREACHABLE",
            "current_config": db_device.current_config,
            "error_note": "Could not fetch real-time blockchain state."
        })
