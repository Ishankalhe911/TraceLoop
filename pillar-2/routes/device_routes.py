import hashlib
import json
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import Dict, Any

from database import get_db, Device, DeviceStatus, User, UserRole, KYCDocument, DocType, DocStatus
from auth import RequireRole, get_current_user
from services.ledger_service import ledger_service

router = APIRouter(prefix="/devices", tags=["Devices"])

def standard_response(data: dict = None, code: int = 200) -> dict:
    return {
        "status": "success",
        "code": code,
        "data": data or {},
        "error": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "traceloop_version": "v1"
    }

def generate_sha256(data: str) -> str:
    return hashlib.sha256(data.encode('utf-8')).hexdigest()

def make_aware(dt: datetime) -> datetime:
    """Ensures a datetime is timezone-aware (UTC). Handles both naive and aware."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class RegisterDevicePayload(BaseModel):
    brand_code: str = Field(..., min_length=2, max_length=4)
    brand_name: str = Field(..., max_length=40)
    serial_raw: str = Field(..., max_length=80)
    original_config: Dict[str, Any]


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register_device(
    payload: RegisterDevicePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.FIRST_BUYER]))
):
    """
    Registers a new device. FIRST_BUYER only.
    Requires admin-approved PURCHASE_PROOF tied specifically to the hardware serial number.
    """
    # 1. Sanitize & Normalize Inputs
    brand_code_upper = payload.brand_code.strip().upper()
    serial_upper = payload.serial_raw.strip().upper()
    device_id = f"TL-{brand_code_upper}-{serial_upper}"

    # 2. Hardware Uniqueness Guard: Prevent duplicate registration of the same physical serial
    existing_device = db.query(Device).filter(
        (Device.id == device_id) | (Device.serial_raw == serial_upper)
    ).first()
    if existing_device:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Device with serial '{serial_upper}' is already registered in Trace-Loop."
        )

    # 3. Cryptographic Hardware-Bound KYC Check
    # Ensures the user cannot use an accepted invoice for Device A to register Device B
    proof = db.query(KYCDocument).filter(
        KYCDocument.user_id == current_user.id,
        KYCDocument.doc_type == DocType.PURCHASE_PROOF,
        KYCDocument.status == DocStatus.ACCEPTED,
        KYCDocument.serial_for_device == serial_upper
    ).first()

    if not proof:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Admin-approved purchase proof required for serial '{serial_upper}'. "
                   "Please upload proof via KYC and await admin approval."
        )

    # 4. Hash PII and hardware config before blockchain write
    serial_hash = generate_sha256(serial_upper)
    owner_hash = generate_sha256(current_user.id)
    config_str = json.dumps(payload.original_config, sort_keys=True)
    config_hash = generate_sha256(config_str)

    try:
        # Chain first — Pillar 1 (Algorand Ledger)
        chain_result = ledger_service.register_device(
            device_id=device_id,
            owner_hash=owner_hash,
            config_hash=config_hash
        )

        # DB after confirmation — Pillar 3 (PostgreSQL State)
        new_device = Device(
            id=device_id,
            serial_hash=serial_hash,
            serial_raw=serial_upper,
            brand_code=brand_code_upper,
            brand_name=payload.brand_name.strip(),
            original_config=payload.original_config,
            current_config=payload.original_config,
            current_owner_id=current_user.id,
            current_owner_hash=owner_hash,
            status=DeviceStatus.REGISTERED,
            stamp_valid=False
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
        raise HTTPException(status_code=500, detail=f"Registration failed: {str(e)}")


@router.get("/my-devices")
def get_my_devices(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Fetches all devices currently owned by the authenticated user, including verification status."""
    devices = db.query(Device).filter(
        Device.current_owner_id == current_user.id
    ).order_by(Device.updated_at.desc()).all()

    results = []
    for d in devices:
        results.append({
            "device_id": d.id,
            "brand": d.brand_name,
            "status": d.status,
            "stamp_valid": d.stamp_valid,
            "is_for_sale": d.is_for_sale,
            "asking_price": float(d.asking_price) if d.asking_price else None,
            "last_verified_at": make_aware(d.last_verified_at).isoformat() if d.last_verified_at else None,
            "current_config": d.current_config,
        })

    return standard_response({
        "total_owned": len(results),
        "devices": results
    })


@router.get("/{device_id}")
def get_device(
    device_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Combined off-chain + on-chain state. Any authenticated active user."""
    db_device = db.query(Device).filter(Device.id == device_id).first()
    if not db_device:
        raise HTTPException(status_code=404, detail="Device not found.")

    try:
        chain_state = ledger_service.get_device_state(device_id)
        return standard_response({
            "device_id": db_device.id,
            "brand": db_device.brand_name,
            "serial_raw": db_device.serial_raw,
            "db_status": db_device.status,
            "chain_status": chain_state["state_label"],
            "stamp_valid": chain_state["stamp_valid"],
            "current_config": db_device.current_config,
            "last_verified_at": make_aware(db_device.last_verified_at).isoformat() if db_device.last_verified_at else None,
            "registered_at": make_aware(db_device.registered_at).isoformat() if db_device.registered_at else None
        })
    except Exception:
        # Graceful degradation if Algorand node is unreachable
        return standard_response({
            "device_id": db_device.id,
            "brand": db_device.brand_name,
            "serial_raw": db_device.serial_raw,
            "db_status": db_device.status,
            "chain_status": "UNREACHABLE",
            "current_config": db_device.current_config,
            "last_verified_at": make_aware(db_device.last_verified_at).isoformat() if db_device.last_verified_at else None,
            "registered_at": make_aware(db_device.registered_at).isoformat() if db_device.registered_at else None,
            "error_note": "Could not fetch real-time blockchain state."
        })