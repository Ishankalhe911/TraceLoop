import hashlib
import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel

# Import frozen models, RBAC, and Ledger Service
from database import (
    get_db, Device, DeviceStatus, User, UserRole, 
    VerificationRequest, VerificationStatus, ManufacturerCache
)
from auth import RequireRole, get_current_user
from services.ledger_service import ledger_service

router = APIRouter(prefix="/verification", tags=["Verification"])

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
class StampDevicePayload(BaseModel):
    device_id: str

# --- ROUTES ---

@router.post("/stamp", status_code=status.HTTP_200_OK)
def issue_verification_stamp(
    payload: StampDevicePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.VERIFIABLE]))
):
    """
    Step 1: Cross-checks device config.
    Step 2: Issues verification stamp to Algorand TestNet.
    Step 3: Unblocks transfers by setting stamp_valid = True in Postgres.
    """
    device = db.query(Device).filter(Device.id == payload.device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found in Trace-Loop.")

    # 1. Compare Configs (Mocking the Physical Check)
    # In a real-world scenario, the verifier inputs the physical specs. 
    # For MVP, we validate the DB's current_config against the ManufacturerCache.
    cached_mfg = db.query(ManufacturerCache).filter(
        ManufacturerCache.serial_hash == device.serial_hash
    ).first()

    differences = []
    if cached_mfg:
        mfg_config = cached_mfg.original_config
        curr_config = device.current_config
        all_components = set(mfg_config.keys()).union(curr_config.keys())
        
        for comp in all_components:
            if mfg_config.get(comp) != curr_config.get(comp):
                differences.append({
                    "component": comp,
                    "manufacturer_value": mfg_config.get(comp),
                    "current_value": curr_config.get(comp)
                })

    # If unexplained differences exist, the verifier should technically use the /reject route.
    # But if they proceed to stamp it, they are legally confirming the current_config is accurate.
    
    # 2. Hash the confirmed configuration for the blockchain
    config_str = json.dumps(device.current_config, sort_keys=True)
    confirmed_config_hash = generate_sha256(config_str)

    try:
        # 3. Pre-flight: ask chain if verification is allowed (free simulate)
        if not ledger_service.can_verify(device.id):
            raise HTTPException(
                status_code=409,
                detail="Chain pre-check failed: device cannot be verified in its current on-chain state."
            )

        # 4. Synchronous Blockchain Mutation (Pillar 1)
        # Executes ARC-4 App call to PuyaPy contract[cite: 4, 6]
        chain_result = ledger_service.verify_device(
            device_id=device.id,
            confirmed_config_hash=confirmed_config_hash
        )

        # 4. Off-Chain Database Storage (Pillar 3)
        device.status = DeviceStatus.VERIFIED
        device.stamp_valid = True  # CRITICAL: This unblocks the transfer_routes
        device.last_verified_at = datetime.utcnow()
        device.last_verified_by = current_user.id

        # 5. Record the Verification Request / History
        verification_record = VerificationRequest(
            device_id=device.id,
            requested_by=device.current_owner_id,
            verifier_id=current_user.id,
            status=VerificationStatus.STAMPED,
            config_snapshot=device.current_config,
            manufacturer_match=(len(differences) == 0),
            stamped_at=datetime.utcnow()
        )
        
        db.add(verification_record)
        db.commit()

        return standard_response({
            "device_id": device.id,
            "verification_id": verification_record.id,
            "chain_tx_id": chain_result["tx_id"],
            "stamp_valid": True,
            "differences_noted": differences,
            "status": "VERIFIED"
        })

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Ledger verification failed: {str(e)}"
        )


@router.get("/device/{device_id}/history")
def get_verification_history(
    device_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Fetches all verification stamps for a specific device[cite: 7]."""
    history = db.query(VerificationRequest).filter(
        VerificationRequest.device_id == device_id
    ).order_by(VerificationRequest.created_at.desc()).all()

    return standard_response({
        "device_id": device_id,
        "stamp_history": [
            {
                "verification_id": req.id,
                "verifier_id": req.verifier_id,
                "status": req.status,
                "stamped_at": req.stamped_at.isoformat() + "Z" if req.stamped_at else None,
                "manufacturer_match": req.manufacturer_match
            } for req in history
        ]
    })


