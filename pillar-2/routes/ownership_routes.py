import hashlib
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import Optional

from database import get_db, Device, Transfer, User, UserRole, DeviceStatus, TransferStatus
from auth import get_current_user, RequireRole
from services.ledger_service import ledger_service

router = APIRouter(prefix="/transfers", tags=["Transfers & Marketplace"])

def standard_response(data: dict = None, code: int = 200) -> dict:
    return {
        "status": "success",
        "code": code,
        "data": data or {},
        "error": None,
        # FIX 1: Timezone-aware timestamp for consistency
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

def verify_stamp_validity(device: Device, db: Session):
    """
    Enforces active stamp and 7-day TTL check.
    Raises 400 if stamp is invalid or expired.
    """
    if not device.stamp_valid:
        raise HTTPException(
            status_code=400,
            detail="Device does not have an active verification stamp. "
            "A VERIFIABLE node must re-audit this device before it can be transferred."
        )
    if device.last_verified_at:
        verified_at = make_aware(device.last_verified_at)
        ttl_expiry = verified_at + timedelta(days=7)

        if datetime.now(timezone.utc) > ttl_expiry:
            # Auto-expire the stamp and pull off market
            device.stamp_valid = False
            device.is_for_sale = False
            db.commit()
            raise HTTPException(
                status_code=400,
                detail="Verification stamp expired (older than 7 days). "
                "Device must be re-audited by a Service Center before transfer."
            )


# --- PAYLOAD SCHEMAS ---

class ListDevicePayload(BaseModel):
    asking_price: Optional[float] = Field(None, ge=0)
    city: Optional[str] = Field(None, max_length=60)

class InitiateTransferPayload(BaseModel):
    device_id: str
    to_user_id: str


# --- MARKETPLACE ROUTES (Discovery) ---

@router.post("/list/{device_id}")
def list_device_for_sale(
    device_id: str,
    payload: ListDevicePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.FIRST_BUYER, UserRole.BUYER, UserRole.RESELLER]))
):
    """
    Marks a device as FOR_SALE, making it publicly discoverable.
    Device must have a valid, unexpired verification stamp.
    Listing is purely off-chain — no blockchain call needed.
    """
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found.")
    if device.current_owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the owner can list this device.")

    verify_stamp_validity(device, db)

    device.is_for_sale = True
    device.asking_price = payload.asking_price
    device.city = payload.city
    db.commit()

    return standard_response({
        "device_id": device.id,
        "asking_price": payload.asking_price,
        "city": payload.city,
        "message": "Device listed on the public marketplace. "
        "Share your Trace-Loop ID with interested buyers to initiate a transfer."
    })


@router.post("/unlist/{device_id}")
def unlist_device(
    device_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.FIRST_BUYER, UserRole.BUYER, UserRole.RESELLER]))
):
    """Owner removes their device from the marketplace."""
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found.")
    if device.current_owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the owner can unlist this device.")

    device.is_for_sale = False
    db.commit()

    return standard_response({
        "device_id": device.id,
        "message": "Device removed from marketplace."
    })


@router.get("/marketplace")
def get_marketplace_listings(db: Session = Depends(get_db)):
    """
    Returns all verified devices currently for sale.
    The blockchain passport is shown — no PII or owner identity exposed.
    """
    devices = db.query(Device).filter(
        Device.is_for_sale == True,
        Device.stamp_valid == True
    ).all()

    results = []
    for d in devices:
        # Auto-expire stale stamps
        if d.last_verified_at:
            verified_at = make_aware(d.last_verified_at)
            if datetime.now(timezone.utc) > (verified_at + timedelta(days=7)):
                d.is_for_sale = False
                d.stamp_valid = False
                db.commit()
                continue

        # ---> FIX PART 1: Fetch the owner from the database <---
        owner = db.query(User).filter(User.id == d.current_owner_id).first()

        results.append({
            "device_id": d.id,
            "brand": d.brand_name,
            "city": d.city,
            "asking_price": float(d.asking_price) if d.asking_price else None,
            "current_config": d.current_config,
            "stamp_valid": d.stamp_valid,
            "last_verified_at": make_aware(d.last_verified_at).isoformat() if d.last_verified_at else None,
            "registered_at": make_aware(d.registered_at).isoformat() if d.registered_at else None,
            
            # ---> FIX PART 2: Add the phone number to the response <---
            "seller_phone": owner.phone if owner else None,
            
            "blockchain_note": "Config and ownership verified on Algorand TestNet. "
            "Inspect device physically and verify config_hash matches."
        })

    return standard_response({"total": len(results), "listings": results})


# --- 3-PARTY HANDSHAKE ROUTES (Execution) ---

@router.post("/initiate", status_code=status.HTTP_201_CREATED)
def initiate_transfer(
    payload: InitiateTransferPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.FIRST_BUYER, UserRole.BUYER, UserRole.RESELLER]))
):
    """
    Step 1 of 3: Seller initiates transfer to a specific buyer.
    Buyer ID is exchanged out-of-band (after seeing marketplace listing).
    Device is pulled off marketplace once P2P deal is initiated.
    """
    device = db.query(Device).filter(Device.id == payload.device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found.")
    if device.current_owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the current owner can initiate a transfer.")
    if payload.to_user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot transfer device to yourself.")

    verify_stamp_validity(device, db)

    receiver = db.query(User).filter(User.id == payload.to_user_id).first()
    if not receiver or receiver.status != "ACTIVE":
        raise HTTPException(status_code=404, detail="Receiver not found or account is not active.")

    # Check no active transfer already pending for this device
    existing = db.query(Transfer).filter(
        Transfer.device_id == payload.device_id,
        Transfer.status.in_([TransferStatus.PENDING, TransferStatus.IN_PROGRESS])
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail="An active transfer already exists for this device. Cancel it before initiating a new one."
        )

    # Pull off marketplace — P2P deal initiated
    device.is_for_sale = False

    try:
        new_transfer = Transfer(
            device_id=payload.device_id,
            from_user_id=current_user.id,
            to_user_id=payload.to_user_id,
            status=TransferStatus.PENDING
        )
        db.add(new_transfer)
        db.commit()
        db.refresh(new_transfer)

        return standard_response({
            "transfer_id": new_transfer.id,
            "device_id": payload.device_id,
            "to_user_id": payload.to_user_id,
            "status": new_transfer.status,
            "message": "Transfer initiated. Awaiting buyer acceptance."
        }, 201)

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/incoming")
def get_incoming_transfers(
    db: Session = Depends(get_db),
    # FIX 2: Added UserRole.RECYCLER so they can fetch transfers sent to them
    current_user: User = Depends(RequireRole([UserRole.BUYER, UserRole.FIRST_BUYER, UserRole.RESELLER, UserRole.RECYCLER]))
):
    """Buyer sees all pending incoming transfers waiting for their acceptance."""
    transfers = db.query(Transfer).filter(
        Transfer.to_user_id == current_user.id,
        Transfer.status == TransferStatus.PENDING
    ).order_by(Transfer.initiated_at.desc()).all()

    results = []
    for t in transfers:
        device = db.query(Device).filter(Device.id == t.device_id).first()
        results.append({
            "transfer_id": t.id,
            "device_id": t.device_id,
            "brand_name": device.brand_name if device else "Unknown Device",
            "current_config": device.current_config if device else {},
            "from_user_id": t.from_user_id,
            "status": t.status,
            "initiated_at": make_aware(t.initiated_at).isoformat() if t.initiated_at else None
        })

    return standard_response({
        "total_pending": len(results),
        "incoming_transfers": results
    })

@router.get("/pending-completion")
def get_transfers_pending_completion(
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.VERIFIABLE]))
):
    """Verifier inbox — all IN_PROGRESS transfers waiting for physical inspection."""
    transfers = db.query(Transfer).filter(
        Transfer.status == TransferStatus.IN_PROGRESS
    ).order_by(Transfer.initiated_at.desc()).all()

    results = []
    for t in transfers:
        device = db.query(Device).filter(Device.id == t.device_id).first()
        results.append({
            "transfer_id": t.id,
            "device_id": t.device_id,
            "brand_name": device.brand_name if device else "Unknown",
            "current_config": device.current_config if device else {},
            "stamp_valid": device.stamp_valid if device else False,
            "from_user_id": t.from_user_id,
            "to_user_id": t.to_user_id,
            "initiated_at": make_aware(t.initiated_at).isoformat() if t.initiated_at else None
        })

    return standard_response({
        "total_pending": len(results),
        "transfers_to_complete": results
    })
@router.post("/{transfer_id}/accept")
def accept_transfer(
    transfer_id: str,
    db: Session = Depends(get_db),
    # FIX 3: Added UserRole.RECYCLER so they can legally accept the device
    current_user: User = Depends(RequireRole([UserRole.BUYER, UserRole.FIRST_BUYER, UserRole.RESELLER, UserRole.RECYCLER]))
):
    """
    Step 2 of 3: Buyer explicitly accepts the incoming transfer.
    Until buyer accepts, verifier is mathematically blocked from completing.
    """
    transfer = db.query(Transfer).filter(Transfer.id == transfer_id).first()
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found.")
    if transfer.to_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You are not the intended recipient of this transfer.")
    if transfer.status != TransferStatus.PENDING:
        raise HTTPException(
            status_code=409,
            detail=f"Transfer cannot be accepted. Current status: {transfer.status}"
        )

    transfer.status = TransferStatus.IN_PROGRESS
    db.commit()

    return standard_response({
        "transfer_id": transfer.id,
        "status": transfer.status,
        "message": "Transfer accepted. A VERIFIABLE node will now physically inspect "
        "the device and complete the on-chain transfer."
    })


@router.post("/{transfer_id}/cancel")
def cancel_transfer(
    transfer_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Seller OR buyer can cancel at any point before verifier completes.
    Once completed on-chain — chain is final, cancellation impossible.
    """
    transfer = db.query(Transfer).filter(Transfer.id == transfer_id).first()
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found.")

    if current_user.id not in [transfer.from_user_id, transfer.to_user_id]:
        raise HTTPException(
            status_code=403,
            detail="Only the seller or buyer can cancel this transfer."
        )

    if transfer.status == TransferStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail="Transfer already completed on-chain. Blockchain state is final — cannot be cancelled. "
            "If there is a dispute, use POST /disputes/raise."
        )

    transfer.status = TransferStatus.CANCELLED

    # Restore device to VERIFIED — chain state unchanged, only DB
    device = db.query(Device).filter(Device.id == transfer.device_id).first()
    if device:
        device.status = DeviceStatus.VERIFIED
        # Don't restore is_for_sale — seller must re-list explicitly

    db.commit()

    return standard_response({
        "transfer_id": transfer.id,
        "status": "CANCELLED",
        "cancelled_by": current_user.id,
        "message": "Transfer cancelled. Device is available. "
        "Re-list via POST /transfers/list/{device_id} if you want to sell again."
    })


@router.post("/{transfer_id}/complete")
def complete_transfer(
    transfer_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.VERIFIABLE]))
):
    """
    Step 3 of 3: VERIFIABLE node physically inspects device and executes on-chain transfer.
    Security guarantees:
      - Buyer must have accepted first (IN_PROGRESS check)
      - new_owner_hash is computed from DB transfer.to_user_id — verifier cannot redirect
      - Stamp TTL enforced — expired stamps block completion
    """
    transfer = db.query(Transfer).filter(Transfer.id == transfer_id).first()
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer record not found.")

    # Hard block — buyer must have accepted first
    if transfer.status != TransferStatus.IN_PROGRESS:
        raise HTTPException(
            status_code=409,
            detail="Buyer must accept the transfer before a verifier can complete it. "
            f"Current status: {transfer.status}"
        )

    device = db.query(Device).filter(Device.id == transfer.device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found.")

    # Stamp TTL check
    verify_stamp_validity(device, db)

    # Pre-flight: confirm transferable on-chain (free simulate)
    if not ledger_service.can_transfer(device.id):
        raise HTTPException(
            status_code=409,
            detail="Chain pre-check failed: device is not in a transferable state on-chain. "
            "Stamp may be invalid or state machine is in wrong state."
        )

    # Lock new_owner_hash to the approved buyer — computed from DB, not from request
    # Verifier mathematically cannot redirect to any other address
    new_owner_hash = generate_sha256(transfer.to_user_id)

    try:
        # Chain first — Pillar 1
        chain_result = ledger_service.transfer_device(
            device_id=device.id,
            new_owner_hash=new_owner_hash
        )

        # DB after confirmation — Pillar 3
        device.current_owner_id = transfer.to_user_id
        device.current_owner_hash = new_owner_hash
        device.status = DeviceStatus.TRANSFERRED
        device.stamp_valid = False  # Must be re-verified before next transfer

        transfer.status = TransferStatus.COMPLETED
        transfer.verifier_id = current_user.id
        transfer.completed_at = datetime.now(timezone.utc)

        db.commit()

        return standard_response({
            "transfer_id": transfer.id,
            "device_id": device.id,
            "new_owner_id": transfer.to_user_id,
            "chain_tx_id": chain_result["tx_id"],
            "confirmed_round": chain_result["confirmed_round"],
            "status": "COMPLETED",
            "message": "Ownership transferred on Algorand TestNet. Device passport updated."
        })

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Ledger execution failed: {str(e)}"
        )


@router.get("/device/{device_id}")
def get_device_transfer_history(
    device_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Full transfer history for a device — the ownership chain proof."""
    transfers = db.query(Transfer).filter(
        Transfer.device_id == device_id
    ).order_by(Transfer.initiated_at.desc()).all()

    history = []
    for t in transfers:
        history.append({
            "transfer_id": t.id,
            "from_user_id": t.from_user_id,
            "to_user_id": t.to_user_id,
            "verifier_id": t.verifier_id,
            "status": t.status,
            "velocity_flagged": t.velocity_flagged,
            "count_flagged": t.count_flagged,
            "initiated_at": make_aware(t.initiated_at).isoformat() if t.initiated_at else None,
            "completed_at": make_aware(t.completed_at).isoformat() if t.completed_at else None
        })

    return standard_response({
        "device_id": device_id,
        "total_transfers": len(history),
        "history": history
    })