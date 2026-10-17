import hashlib
import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import Optional

# Import frozen models, RBAC, and Ledger Service
from database import get_db, Device, DeviceHardwareLog, ChangeType, User, DeviceStatus, RegistrationStatus
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
    component: str = Field(..., min_length=1, max_length=80, description="E.g., RAM, Storage, Battery")
    change_type: ChangeType
    # Accepted for backward compatibility, but the server never trusts this value.
    # The authoritative old specification is read from Device.current_config.
    old_spec: Optional[str] = Field(None, description="Previous spec (informational only)")
    new_spec: str = Field(..., min_length=1, max_length=200, description="The newly installed spec")


# --- ROUTES ---

@router.post("/{device_id}/hardware-change", status_code=status.HTTP_200_OK)
def declare_hardware_change(
    device_id: str,
    payload: HardwareChangePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Current owner declares a hardware change.

    The server derives the authoritative old specification from the current
    device configuration, updates the configuration, writes the new config hash
    on-chain, appends the hardware log, and invalidates the current verification
    stamp so a service-center re-verification is required before resale.
    """
    device = db.query(Device).filter(Device.public_id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found.")
    if device.registration_status != RegistrationStatus.CONFIRMED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Device has no confirmed Phase 2 on-chain registration; hardware changes are blocked.",
        )

    # Terminal state guard — no further hardware mutations are possible.
    if device.status in [DeviceStatus.RECYCLED, DeviceStatus.EXPORTED]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot modify a terminal device. Device has been RECYCLED or EXPORTED."
        )

    # Only the current owner may declare a hardware change.
    if device.current_owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the current device owner can declare hardware changes."
        )

    # Disputed devices are frozen until the dispute is resolved.
    if device.status == DeviceStatus.DISPUTED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Hardware changes are blocked while the device is in a DISPUTED state."
        )

    component = payload.component.strip()
    if not component:
        raise HTTPException(status_code=422, detail="Component name cannot be empty.")

    updated_config = dict(device.current_config or {})

    # Resolve a component key case-insensitively so `RAM` and `ram` do not
    # create duplicate logical fields.
    existing_key = next(
        (k for k in updated_config.keys() if str(k).lower() == component.lower()),
        None
    )

    actual_old_spec = updated_config.get(existing_key) if existing_key else None
    change_type = payload.change_type

    # Enforce semantically correct change types.
    if change_type in [ChangeType.UPGRADED, ChangeType.REPLACED, ChangeType.REMOVED] and existing_key is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot {change_type.value.lower()} '{component}': component is not present in the current configuration."
        )

    if change_type == ChangeType.ADDED and existing_key is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Component '{existing_key}' already exists. Use UPGRADED or REPLACED instead."
        )

    # Apply the change to the current configuration. The client-provided
    # old_spec is intentionally ignored for integrity; the server records the
    # actual previous value from the database.
    if change_type == ChangeType.REMOVED:
        del updated_config[existing_key]
        logged_new_spec = "REMOVED"
    elif existing_key is not None:
        updated_config[existing_key] = payload.new_spec.strip()
        logged_new_spec = payload.new_spec.strip()
    else:
        updated_config[component] = payload.new_spec.strip()
        logged_new_spec = payload.new_spec.strip()

    config_str = json.dumps(updated_config, sort_keys=True)
    new_config_hash = generate_sha256(config_str)

    try:
        # Pillar 1: operator-signed contract call. The authenticated user is
        # the business actor; the configured operator account commits the
        # protocol mutation to Algorand.
        chain_result = ledger_service.declare_hardware_change(
            public_id=device.public_id,
            new_config_hash=new_config_hash
        )

        # Pillar 3: mirror the new config and append the audit record only after
        # the blockchain transaction succeeds.
        device.current_config = updated_config
        device.stamp_valid = False

        log_entry = DeviceHardwareLog(
            device_id=device.id,
            component=existing_key or component,
            change_type=change_type,
            old_spec=None if actual_old_spec is None else str(actual_old_spec),
            new_spec=logged_new_spec,
            declared_by=current_user.id,
            stamp_invalidated=True
        )
        db.add(log_entry)
        db.commit()

        return standard_response({
            "device_id": device.public_id,
            "component": existing_key or component,
            "change_type": change_type.value,
            "old_spec": None if actual_old_spec is None else str(actual_old_spec),
            "new_spec": logged_new_spec,
            "current_config": updated_config,
            "chain_tx_id": chain_result["tx_id"],
            "stamp_valid": False,
            "message": "Hardware change recorded on-chain. Re-verification is required before listing or the next ownership transfer."
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
    """Fetches the append-only hardware change log for a device."""
    device = db.query(Device).filter(Device.public_id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found.")

    logs = db.query(DeviceHardwareLog).filter(
        DeviceHardwareLog.device_id == device.id
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
                "declared_by": log.declared_by,
                "declared_at": log.created_at.isoformat() + "Z"
            } for log in logs
        ]
    })