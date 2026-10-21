import hashlib
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import Optional
from database import (
    get_db, Device, Transfer, User, UserRole, DeviceStatus, TransferStatus,
    UserStatus, RegistrationStatus, IdentityVersion, LedgerEvent, ChainStatus
)
from services.ledger_service import LedgerSubmissionError
from auth import get_current_user, RequireRole
from services.ledger_service import ledger_service
from services.device_policy import require_confirmed_v2_device, is_confirmed_v2_device
from services.notification_service import create_notification
from routes.evidence_routes import assert_active_role_evidence
from database import EvidenceType

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

def require_confirmed_registration(device: Device, action: str) -> None:
    """Require both V2 identity mode and confirmed on-chain registration."""
    require_confirmed_v2_device(device, action)


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
    if not device.last_verified_at:
        # A valid flag without a timestamp cannot establish that the 7-day TTL is met.
        device.stamp_valid = False
        device.is_for_sale = False
        try:
            db.commit()
        except Exception:
            db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Verification timestamp is missing. Device must be re-audited before transfer."
        )

    verified_at = make_aware(device.last_verified_at)
    ttl_expiry = verified_at + timedelta(days=7)
    if datetime.now(timezone.utc) > ttl_expiry:
        device.stamp_valid = False
        device.is_for_sale = False
        try:
            db.commit()
        except Exception:
            db.rollback()
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
    device_id: str = Field(..., min_length=1, max_length=40)
    to_user_id: str = Field(..., min_length=1, max_length=36)


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
    device = db.query(Device).filter(Device.public_id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found.")
    require_confirmed_registration(device, "marketplace listing")
    if device.current_owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the owner can list this device.")

    verify_stamp_validity(device, db)

    device.is_for_sale = True
    device.asking_price = payload.asking_price
    device.city = payload.city
    db.commit()

    return standard_response({
        "device_id": device.public_id,
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
    device = db.query(Device).filter(Device.public_id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found.")
    require_confirmed_registration(device, "marketplace unlisting")
    if device.current_owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the owner can unlist this device.")

    device.is_for_sale = False
    db.commit()

    return standard_response({
        "device_id": device.public_id,
        "message": "Device removed from marketplace."
    })

@router.get("/marketplace/auth")
def get_marketplace_listings_auth(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)  # Strict auth enforcement
):
    """
    Authenticated marketplace: returns seller phone numbers exclusively for logged-in users.
    DPDPA Compliant: PII is only exposed to verified accounts, not public scrapers.
    """
    if current_user.status != UserStatus.ACTIVE:
        raise HTTPException(status_code=403, detail="Account is not active.")

    devices = db.query(Device).filter(
        Device.is_for_sale.is_(True),
        Device.stamp_valid.is_(True),
        Device.registration_status == RegistrationStatus.CONFIRMED,
        Device.identity_version == IdentityVersion.V2_PUBLIC_ID,
    ).all()

    results = []
    for d in devices:
        # Fail closed if a stamp timestamp is missing or older than the 7-day TTL.
        if not d.last_verified_at:
            continue
        verified_at = make_aware(d.last_verified_at)
        if datetime.now(timezone.utc) > (verified_at + timedelta(days=7)):
            continue

        # Fetch the owner securely to attach their phone number
        owner = db.query(User).filter(User.id == d.current_owner_id).first()
        
        results.append({
            "device_id": d.public_id,
            "brand": d.brand_name,
            "city": d.city,
            "asking_price": float(d.asking_price) if d.asking_price else None,
            "current_config": d.current_config,
            "stamp_valid": d.stamp_valid,
            "last_verified_at": make_aware(d.last_verified_at).isoformat() if d.last_verified_at else None,
            "registered_at": make_aware(d.registered_at).isoformat() if d.registered_at else None,
            
            # THE FIX: Phone number is exposed ONLY because current_user is authenticated
            "seller_phone": owner.phone if owner else None, 
            
            "blockchain_note": "Config and ownership verified on Algorand TestNet."
        })

    return standard_response({"total": len(results), "listings": results})
@router.get("/marketplace")
def get_marketplace_listings(db: Session = Depends(get_db)):
    """
    Returns all verified devices currently for sale.
    The blockchain passport is shown — no PII or owner identity exposed.
    """
    devices = db.query(Device).filter(
        Device.is_for_sale.is_(True),
        Device.stamp_valid.is_(True),
        Device.registration_status == RegistrationStatus.CONFIRMED,
        Device.identity_version == IdentityVersion.V2_PUBLIC_ID,
    ).all()

    results = []
    for d in devices:
        # Fail closed if a stamp timestamp is missing or older than the 7-day TTL.
        if not d.last_verified_at:
            continue
        verified_at = make_aware(d.last_verified_at)
        if datetime.now(timezone.utc) > (verified_at + timedelta(days=7)):
            continue

        results.append({
            "device_id": d.public_id,
            "brand": d.brand_name,
            "city": d.city,
            "asking_price": float(d.asking_price) if d.asking_price else None,
            "current_config": d.current_config,
            "stamp_valid": d.stamp_valid,
            "last_verified_at": make_aware(d.last_verified_at).isoformat() if d.last_verified_at else None,
            "registered_at": make_aware(d.registered_at).isoformat() if d.registered_at else None,
            
            # NOTE: seller_phone and owner lookup have been completely removed!
            # This ensures 100% compliance with DPDPA privacy laws.
            
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
    device = db.query(Device).filter(Device.public_id == payload.device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found.")
    require_confirmed_registration(device, "transfer initiation")
    if device.current_owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the current owner can initiate a transfer.")
    recipient_id = payload.to_user_id.strip()
    if not recipient_id:
        raise HTTPException(status_code=422, detail="A recipient TraceLoop ID is required.")
    if recipient_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot transfer device to yourself.")

    verify_stamp_validity(device, db)

    receiver = db.query(User).filter(User.id == recipient_id).first()
    if not receiver or receiver.status != UserStatus.ACTIVE:
        raise HTTPException(status_code=404, detail="Receiver not found or account is not active.")
    if receiver.role not in {
        UserRole.FIRST_BUYER,
        UserRole.BUYER,
        UserRole.RESELLER,
        UserRole.RECYCLER,
    }:
        raise HTTPException(
            status_code=422,
            detail="Transfers may only be addressed to an active buyer, reseller, or recycler account.",
        )

    # Check no active transfer already pending for this device
    existing = db.query(Transfer).filter(
        Transfer.device_id == device.id,
        Transfer.status.in_([TransferStatus.PENDING, TransferStatus.IN_PROGRESS])
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail="An active transfer already exists for this device. Cancel it before initiating a new one."
        )

    # --- FRAUD SIGNAL 1: Transfer Velocity ---
    # SOP Section 5.3: >2 outgoing transfers from same BUYER in 30 days → flag
    velocity_flagged = False
    if current_user.role == UserRole.BUYER:
        thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
        recent_outgoing = db.query(Transfer).filter(
            Transfer.from_user_id == current_user.id,
            Transfer.initiated_at >= thirty_days_ago,
            Transfer.status != TransferStatus.CANCELLED
        ).count()
        if recent_outgoing > 2:
            velocity_flagged = True

    # --- FRAUD SIGNAL 2: Device Count ---
    # SOP Section 5.3: receiver holding >3 active devices as BUYER → flag
    count_flagged = False
    if receiver.role == UserRole.BUYER:
        active_device_count = db.query(Device).filter(
            Device.current_owner_id == recipient_id,
            Device.status.notin_([DeviceStatus.RECYCLED, DeviceStatus.EXPORTED])
        ).count()
        if active_device_count > 3:
            count_flagged = True

    # Pull off marketplace — P2P deal initiated
    device.is_for_sale = False

    try:
        # The current valid stamp identifies the verifier the owner selected for
        # this inspection. If the owner wants another service centre, they must
        # explicitly re-verify there first; the transfer is never globally claimable.
        assigned_verifier = device.last_verified_by
        assigned_user = db.query(User).filter(User.id == assigned_verifier).first() if assigned_verifier else None
        if (
            assigned_user is None
            or assigned_user.role != UserRole.VERIFIABLE
            or assigned_user.status != UserStatus.ACTIVE
        ):
            raise HTTPException(
                status_code=409,
                detail="No active verifier is recorded for the current verification stamp. Re-verify with the intended service centre before initiating a transfer.",
            )

        new_transfer = Transfer(
            device_id=device.id,
            from_user_id=current_user.id,
            to_user_id=recipient_id,
            assigned_verifier_id=assigned_user.id,
            status=TransferStatus.PENDING,
            velocity_flagged=velocity_flagged,
            count_flagged=count_flagged
        )
        db.add(new_transfer)
        db.flush()
        create_notification(
            db,
            user_id=receiver.id,
            notification_type="transfer_received",
            title="Incoming transfer request",
            message=f"{device.brand_name} · {device.public_id} is awaiting your acceptance. Review the handoff in Transfers.",
            action_path="/transfer",
            public_device_id=device.public_id,
            entity_id=new_transfer.id,
            dedupe_key=f"transfer:{new_transfer.id}:initiated:{receiver.id}",
        )
        db.commit()
        db.refresh(new_transfer)

        return standard_response({
            "transfer_id": new_transfer.id,
            "device_id": device.public_id,
            "to_user_id": recipient_id,
            "status": new_transfer.status,
            "velocity_flagged": velocity_flagged,
            "count_flagged": count_flagged,
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
            "device_id": device.public_id if device else None,
            "brand_name": device.brand_name if device else "Unknown Device",
            "current_config": device.current_config if device else {},
            "status": t.status,
            "registration_status": device.registration_status.value if device else None,
            "identity_version": (
                device.identity_version.value if device and hasattr(device.identity_version, "value") else None
            ),
            "is_legacy_test": bool(
                device and (
                    device.identity_version == IdentityVersion.V1_LEGACY
                    or device.registration_status == RegistrationStatus.LEGACY_TEST
                )
            ),
            "read_only": bool(device and not is_confirmed_v2_device(device)),
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
    """Only show in-progress transfers explicitly assigned to this verifier."""
    transfers = db.query(Transfer).filter(
        Transfer.status == TransferStatus.IN_PROGRESS,
        Transfer.assigned_verifier_id == current_user.id,
    ).order_by(Transfer.initiated_at.desc()).all()

    results = []
    for t in transfers:
        device = db.query(Device).filter(Device.id == t.device_id).first()
        results.append({
            "transfer_id": t.id,
            "device_id": device.public_id if device else None,
            "brand_name": device.brand_name if device else "Unknown",
            "current_config": device.current_config if device else {},
            "stamp_valid": device.stamp_valid if device else False,
            "registration_status": device.registration_status.value if device else None,
            "identity_version": (
                device.identity_version.value if device and hasattr(device.identity_version, "value") else None
            ),
            "is_legacy_test": bool(
                device and (
                    device.identity_version == IdentityVersion.V1_LEGACY
                    or device.registration_status == RegistrationStatus.LEGACY_TEST
                )
            ),
            "read_only": bool(device and not is_confirmed_v2_device(device)),
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
    device = db.query(Device).filter(Device.id == transfer.device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found.")
    require_confirmed_registration(device, "transfer acceptance")
    if transfer.status != TransferStatus.PENDING:
        raise HTTPException(
            status_code=409,
            detail=f"Transfer cannot be accepted. Current status: {transfer.status}"
        )

    transfer.status = TransferStatus.IN_PROGRESS
    device = db.query(Device).filter(Device.id == transfer.device_id).first()
    if device:
        create_notification(
            db,
            user_id=transfer.from_user_id,
            notification_type="transfer_accepted",
            title="Transfer accepted",
            message=f"The recipient accepted {device.brand_name} · {device.public_id}. An authorized service centre must still inspect the device and complete the handoff.",
            action_path="/transfer",
            public_device_id=device.public_id,
            entity_id=transfer.id,
            dedupe_key=f"transfer:{transfer.id}:accepted:{transfer.from_user_id}",
        )
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

    device = db.query(Device).filter(Device.id == transfer.device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found.")
    require_confirmed_registration(device, "transfer cancellation")

    if getattr(transfer, "chain_attempt_status", None) in {
        "SUBMISSION_STARTED", "SUBMISSION_UNKNOWN", "CONFIRMED_DB_PENDING"
    }:
        raise HTTPException(
            status_code=409,
            detail="Transfer has a chain attempt requiring reconciliation and cannot be cancelled automatically."
        )

    if transfer.status == TransferStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail="Transfer already completed on-chain. Blockchain state is final — cannot be cancelled. "
            "If there is a dispute, use POST /disputes/raise."
        )

    transfer.status = TransferStatus.CANCELLED

    # Restore device to VERIFIED — chain state unchanged, only DB.
    device.status = DeviceStatus.VERIFIED
    # Don't restore is_for_sale — seller must re-list explicitly.
    for participant_id in {transfer.from_user_id, transfer.to_user_id} - {current_user.id, None}:
        create_notification(
            db,
            user_id=participant_id,
            notification_type="transfer_cancelled",
            title="Transfer cancelled",
            message=f"The transfer for {device.brand_name} · {device.public_id} was cancelled before on-chain completion.",
            action_path="/track?id=" + device.public_id,
            public_device_id=device.public_id,
            entity_id=transfer.id,
            dedupe_key=f"transfer:{transfer.id}:cancelled:{participant_id}",
        )

    db.commit()

    return standard_response({
        "transfer_id": transfer.id,
        "status": "CANCELLED",
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

    # Fail closed: assignment is distinct from the verifier who completes it.
    # This check must happen before any chain preflight or submission.
    if transfer.assigned_verifier_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="This transfer is not assigned to your service centre. The owner must re-verify with the intended centre before transfer initiation.",
        )

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

    require_confirmed_registration(device, "transfer completion")
    assert_active_role_evidence(
        current_user, EvidenceType.BRAND_AUTHORIZATION, db, transition_account_on_failure=True
    )

    # Stamp TTL check
    verify_stamp_validity(device, db)

    # Pre-flight: confirm transferable on-chain (free simulate)
    if not ledger_service.can_transfer(device.public_id):
        raise HTTPException(
            status_code=409,
            detail="Chain pre-check failed: device is not in a transferable state on-chain. "
            "Stamp may be invalid or state machine is in wrong state."
        )

    # Lock the new owner hash to the buyer approved in the database, never request input.
    new_owner_hash = generate_sha256(transfer.to_user_id)

    # Do not retry a transfer whose prior attempt may have reached the chain.
    if getattr(transfer, "chain_attempt_status", None) in {
        "SUBMISSION_STARTED", "SUBMISSION_UNKNOWN", "CONFIRMED_DB_PENDING", "COMPLETED"
    }:
        raise HTTPException(
            status_code=409,
            detail="This transfer has a previous chain attempt requiring manual reconciliation. Do not retry."
        )

    new_owner_hash = generate_sha256(transfer.to_user_id)
    attempted_at = datetime.now(timezone.utc)

    # Persist the attempt latch before calling the chain. This requires the companion
    # migration adding the chain_attempt_* columns to transfers.
    transfer.chain_attempt_status = "SUBMISSION_STARTED"
    transfer.chain_attempted_at = attempted_at
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Could not record transfer attempt; no chain call was made.")

    try:
        chain_result = ledger_service.transfer_device(
            public_id=device.public_id,
            new_owner_hash=new_owner_hash,
        )
    except LedgerSubmissionError as exc:
        db.rollback()
        # Re-query and persist the result classification. Never automatically resubmit.
        transfer = db.query(Transfer).filter(Transfer.id == transfer_id).first()
        if transfer:
            transfer.chain_attempt_status = (
                "SUBMISSION_UNKNOWN" if exc.submission_attempted else "PRE_SUBMISSION_FAILED"
            )
            transfer.chain_error_code = exc.error_code
            try:
                db.commit()
            except Exception:
                db.rollback()
        if exc.submission_attempted:
            raise HTTPException(
                status_code=409,
                detail="Transfer outcome is uncertain. Manual reconciliation is required; do not retry."
            )
        raise HTTPException(status_code=502, detail="Ledger transaction was not submitted. Review the transfer before retrying.")
    except Exception:
        db.rollback()
        transfer = db.query(Transfer).filter(Transfer.id == transfer_id).first()
        if transfer:
            transfer.chain_attempt_status = "SUBMISSION_UNKNOWN"
            transfer.chain_error_code = "UNCLASSIFIED_LEDGER_EXCEPTION"
            try:
                db.commit()
            except Exception:
                db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Transfer outcome is uncertain. Manual reconciliation is required; do not retry."
        )

    # First persist chain confirmation metadata separately. If the later local ownership
    # update fails, operators still have the tx id and confirmed round for reconciliation.
    transfer.chain_attempt_status = "CONFIRMED_DB_PENDING"
    transfer.chain_tx_id = chain_result.get("tx_id")
    transfer.chain_confirmed_round = chain_result.get("confirmed_round")
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Chain transfer confirmed but local confirmation metadata could not be committed. Manual reconciliation required."
        )

    try:
        device.current_owner_id = transfer.to_user_id
        device.current_owner_hash = new_owner_hash
        device.status = DeviceStatus.TRANSFERRED
        device.stamp_valid = False
        device.is_for_sale = False

        transfer.status = TransferStatus.COMPLETED
        transfer.verifier_id = current_user.id
        transfer.completed_at = datetime.now(timezone.utc)
        transfer.chain_attempt_status = "COMPLETED"
        create_notification(
            db,
            user_id=transfer.from_user_id,
            notification_type="transfer_completed",
            title="Ownership transfer completed",
            message=f"{device.brand_name} · {device.public_id} was transferred to the recipient. The previous ownership record remains in its lifecycle history.",
            action_path="/track?id=" + device.public_id,
            public_device_id=device.public_id,
            entity_id=transfer.id,
            dedupe_key=f"transfer:{transfer.id}:completed:{transfer.from_user_id}",
        )
        create_notification(
            db,
            user_id=transfer.to_user_id,
            notification_type="transfer_completed",
            title="Device transferred to you",
            message=f"{device.brand_name} · {device.public_id} is now recorded under your account. Review the passport and its verification status before your next handoff.",
            action_path="/track?id=" + device.public_id,
            public_device_id=device.public_id,
            entity_id=transfer.id,
            dedupe_key=f"transfer:{transfer.id}:completed:{transfer.to_user_id}",
        )
        db.commit()
    except Exception:
        db.rollback()
        # The chain is already confirmed; leave the attempt latched for manual recovery.
        raise HTTPException(
            status_code=409,
            detail="Chain transfer confirmed but local ownership update failed. Manual reconciliation required; do not retry."
        )

    return standard_response({
        "transfer_id": transfer.id,
        "device_id": device.public_id,
        "chain_tx_id": chain_result.get("tx_id"),
        "confirmed_round": chain_result.get("confirmed_round"),
        "status": "COMPLETED",
        "message": "Ownership transfer confirmed on Algorand; local passport updated.",
    })


@router.get("/device/{device_id}")
def get_device_transfer_history(
    device_id: str,
    db: Session = Depends(get_db),
):
    """Public-safe lifecycle timeline from confirmed ledger mirror events only.

    ``device_id`` is the public Passport ID. This sanitized timeline is intentionally
    public; private Transfer workflow rows are never used as its source. Only allowlisted
    event fields are returned.
    """
    device = db.query(Device).filter(Device.public_id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found.")

    events = (
        db.query(LedgerEvent)
        .filter(
            LedgerEvent.device_id == device.id,
            LedgerEvent.chain_status == ChainStatus.CONFIRMED,
            LedgerEvent.event_type.in_(["REGISTERED", "VERIFIED", "TRANSFERRED", "HARDWARE_CHANGED", "RECYCLED", "EXPORTED"]),
        )
        .order_by(LedgerEvent.timestamp.desc())
        .all()
    )

    history = []
    for event in events:
        # Never expose dispute statuses, raw event metadata, actor IDs, or serials.
        history.append({
            "event_type": event.event_type,
            "transaction_id": event.tx_hash,
            "confirmed_round": event.block_number,
            "timestamp": make_aware(event.confirmed_at or event.timestamp).isoformat()
                if (event.confirmed_at or event.timestamp) else None,
        })

    return standard_response({
        "device_id": device.public_id,
        "brand": device.brand_name,
        "identity_version": device.identity_version.value,
        "is_legacy_test": (
            device.identity_version == IdentityVersion.V1_LEGACY
            or device.registration_status == RegistrationStatus.LEGACY_TEST
        ),
        "read_only": not is_confirmed_v2_device(device),
        "total_events": len(history),
        "history": history,
    })
