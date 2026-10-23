from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db, Device, LedgerEvent, User, IdentityVersion, RegistrationStatus, ChainStatus, DeviceStatus
from auth import get_current_user
from services.ledger_service import ledger_service
from services.stamp_policy import effective_stamp_valid

router = APIRouter(prefix="/ledger", tags=["Blockchain Ledger"])

def standard_response(data: dict = None, code: int = 200) -> dict:
    return {
        "status": "success", "code": code, "data": data or {},
        "error": None, "timestamp": datetime.utcnow().isoformat() + "Z", "traceloop_version": "v1"
    }

def _identity_version_value(device: Device) -> str:
    value = device.identity_version
    return value.value if hasattr(value, "value") else str(value)


_PUBLIC_EVENT_TYPES = {"REGISTERED", "VERIFIED", "TRANSFERRED", "HARDWARE_CHANGED", "RECYCLED", "EXPORTED"}

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
    is_legacy = (
        device.identity_version == IdentityVersion.V1_LEGACY
        or device.registration_status == RegistrationStatus.LEGACY_TEST
    )
    try:
        if is_legacy:
            if not device.legacy_chain_id:
                return standard_response({
                    "device_id": device.public_id,
                    "identity_version": IdentityVersion.V1_LEGACY.value,
                    "state_label": "LEGACY_CHAIN_STATE_UNAVAILABLE",
                    "is_legacy_test": True,
                    "read_only": True,
                    "message": "The historical chain identifier was not recoverable; mirrored history remains available.",
                })
            chain_state = ledger_service.get_legacy_device_state(device.legacy_chain_id)
        else:
            chain_state = ledger_service.get_device_state(device.public_id)

        raw_state = str(chain_state.get("state_label", "UNKNOWN")).upper()
        stamp_state = raw_state
        stamp_valid = chain_state.get("stamp_valid")
        if not is_legacy:
            stamp_valid = effective_stamp_valid(stamp_state, stamp_valid)
        if raw_state == DeviceStatus.DISPUTED.value:
            raw_state = None
        # Never expose owner hashes, config hashes, verifier wallet addresses, or
        # dispute state from this broadly authenticated endpoint.
        return standard_response({
            "device_id": device.public_id,
            "identity_version": _identity_version_value(device),
            "state_label": raw_state,
            "stamp_valid": stamp_valid,
            "updated_at": chain_state.get("updated_at"),
            "is_legacy_test": is_legacy,
            "read_only": is_legacy,
        })
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not retrieve device state from Algorand.") from None

@router.get("/device/{device_id}/events")
def get_offchain_ledger_events(
    device_id: str, 
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Fetches allowlisted lifecycle events from the off-chain mirror. Dispute event
    types are intentionally omitted from this presentation endpoint.
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

    is_legacy_test = (
        device.identity_version == IdentityVersion.V1_LEGACY
        or device.registration_status == RegistrationStatus.LEGACY_TEST
    )
    if not events:
        return standard_response({
            "device_id": device.public_id,
            "identity_version": _identity_version_value(device),
            "events": [],
            "is_legacy_test": is_legacy_test,
            "read_only": not (
                _identity_version_value(device) == IdentityVersion.V2_PUBLIC_ID.value
                and device.registration_status == RegistrationStatus.CONFIRMED
            ),
            "message": "Legacy test record; no mirrored events are stored." if is_legacy_test else None,
        })
        
    visible_events = [e for e in events if str(e.event_type).upper() in _PUBLIC_EVENT_TYPES]
    history = [{
        "event_type": e.event_type,
        "tx_hash": e.tx_hash,
        "block_number": e.block_number,
        "timestamp": e.timestamp.isoformat() + "Z"
    } for e in visible_events]
    
    return standard_response({
        "device_id": device.public_id,
        "identity_version": _identity_version_value(device),
        "events": history,
        "is_legacy_test": is_legacy_test,
        "read_only": not (
            _identity_version_value(device) == IdentityVersion.V2_PUBLIC_ID.value
            and device.registration_status == RegistrationStatus.CONFIRMED
        ),
        "message": (
            "Historical off-chain event mirror for a legacy test passport; the synthetic public ID is not a chain key."
            if is_legacy_test else None
        ),
    })
