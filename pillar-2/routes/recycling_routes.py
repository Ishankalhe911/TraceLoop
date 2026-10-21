"""Recycler workflow and guarded on-chain recycling completion.

The workflow endpoint is a read-only convenience for the frontend. All state
transitions remain enforced by the mutation endpoints and the ledger contract.
"""
from __future__ import annotations

import uuid
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from database import (
    get_db, Device, DeviceStatus, RegistrationStatus, IdentityVersion, User,
    UserRole, UserStatus, RecyclingRecord, RecyclingStatus, Transfer,
    TransferStatus, RewardPoint,
)
from auth import RequireRole
from services.ledger_service import LedgerSubmissionError, ledger_service
from services.device_policy import require_confirmed_v2_device, is_confirmed_v2_device
from services.notification_service import create_notification
from routes.evidence_routes import assert_active_role_evidence
from database import EvidenceType

router = APIRouter(prefix="/recycling", tags=["Recycling"])
logger = logging.getLogger("TraceLoop.Recycling")

_UNCERTAIN_ATTEMPTS = {"SUBMISSION_STARTED", "SUBMISSION_UNKNOWN", "CONFIRMED_DB_PENDING", "COMPLETED"}


def standard_response(data: dict = None, code: int = 200) -> dict:
    return {
        "status": "success",
        "code": code,
        "data": data or {},
        "error": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "traceloop_version": "v1",
    }


def _enum_value(value):
    return value.value if hasattr(value, "value") else value


def _utc_iso(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat()


class ReceiveDevicePayload(BaseModel):
    device_id: str = Field(..., min_length=1, max_length=40)
    condition_notes: str | None = Field(default=None, max_length=2000)


class CompleteRecyclingPayload(BaseModel):
    device_id: str = Field(..., min_length=1, max_length=40)


@router.get("/workflow")
def get_recycler_workflow(
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.RECYCLER])),
):
    """Return a recycler's current work in three stages, without private device IDs."""
    incoming_rows = db.query(Transfer).filter(
        Transfer.to_user_id == current_user.id,
        Transfer.status == TransferStatus.PENDING,
    ).order_by(Transfer.initiated_at.desc()).all()

    incoming = []
    for transfer in incoming_rows:
        device = db.query(Device).filter(Device.id == transfer.device_id).first()
        if not device or not is_confirmed_v2_device(device):
            continue
        incoming.append({
            "transfer_id": transfer.id,
            "device_id": device.public_id,
            "brand_name": device.brand_name,
            "device_status": _enum_value(device.status),
            "current_config": device.current_config or {},
            "initiated_at": _utc_iso(transfer.initiated_at),
        })

    # Accepted recycler handoffs leave PENDING and wait for an authorized verifier.
    # Keep them visible so accepting a transfer never makes the work disappear.
    accepted_rows = db.query(Transfer).filter(
        Transfer.to_user_id == current_user.id,
        Transfer.status == TransferStatus.IN_PROGRESS,
    ).order_by(Transfer.initiated_at.desc()).all()
    awaiting_verifier = []
    for transfer in accepted_rows:
        device = db.query(Device).filter(Device.id == transfer.device_id).first()
        if not device or not is_confirmed_v2_device(device):
            continue
        awaiting_verifier.append({
            "transfer_id": transfer.id,
            "device_id": device.public_id,
            "brand_name": device.brand_name,
            "device_status": _enum_value(device.status),
            "current_config": device.current_config or {},
            "initiated_at": _utc_iso(transfer.initiated_at),
            "message": "Accepted; waiting for the physical handoff and authorized verifier to complete ownership transfer.",
        })

    owned_rows = db.query(Device).filter(
        Device.current_owner_id == current_user.id,
        Device.status == DeviceStatus.TRANSFERRED,
        Device.identity_version == IdentityVersion.V2_PUBLIC_ID,
        Device.registration_status == RegistrationStatus.CONFIRMED,
    ).order_by(Device.updated_at.desc()).all()

    awaiting_receipt = []
    ready_for_recycling = []
    reconciliation_required = []
    for device in owned_rows:
        record = db.query(RecyclingRecord).filter(
            RecyclingRecord.device_id == device.id,
            RecyclingRecord.recycler_id == current_user.id,
        ).first()
        row = {
            "device_id": device.public_id,
            "brand_name": device.brand_name,
            "device_status": _enum_value(device.status),
            "current_config": device.current_config or {},
            "recycling_status": _enum_value(record.recycling_status) if record else None,
            "received_date": _utc_iso(record.received_date) if record else None,
            "completion_date": _utc_iso(record.completion_date) if record else None,
            "certificate_id": record.certificate_id if record else None,
            "chain_attempt_status": record.chain_attempt_status if record else None,
            "chain_tx_id": record.chain_tx_id if record else None,
            "chain_confirmed_round": record.chain_confirmed_round if record else None,
        }
        attempt_status = record.chain_attempt_status if record else None
        if record and attempt_status in {"SUBMISSION_UNKNOWN", "CONFIRMED_DB_PENDING", "SUBMISSION_STARTED"}:
            reconciliation_required.append(row)
        elif record and record.recycling_status == RecyclingStatus.RECEIVED:
            ready_for_recycling.append(row)
        elif record and record.recycling_status == RecyclingStatus.PROCESSING:
            ready_for_recycling.append(row)
        elif record is None or record.recycling_status == RecyclingStatus.SENT:
            awaiting_receipt.append(row)

    return standard_response({
        "incoming_handoffs": incoming,
        "awaiting_verifier": awaiting_verifier,
        "awaiting_receipt": awaiting_receipt,
        "ready_for_recycling": ready_for_recycling,
        "reconciliation_required": reconciliation_required,
        "counts": {
            "incoming_handoffs": len(incoming),
            "awaiting_verifier": len(awaiting_verifier),
            "awaiting_receipt": len(awaiting_receipt),
            "ready_for_recycling": len(ready_for_recycling),
            "reconciliation_required": len(reconciliation_required),
        },
    })


@router.post("/receive", status_code=status.HTTP_200_OK)
def receive_device(
    payload: ReceiveDevicePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.RECYCLER])),
):
    """Record physical receipt after an official ownership transfer to this recycler."""
    device = db.query(Device).filter(Device.public_id == payload.device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found.")
    require_confirmed_v2_device(device, "recycling receipt")

    if device.current_owner_id != current_user.id or device.status != DeviceStatus.TRANSFERRED:
        raise HTTPException(
            status_code=403,
            detail="Device has not been officially transferred to your facility via the verification network.",
        )
    assert_active_role_evidence(
        current_user, EvidenceType.RECYCLER_AUTHORIZATION, db, transition_account_on_failure=True
    )

    record = db.query(RecyclingRecord).filter(
        RecyclingRecord.device_id == device.id,
        RecyclingRecord.recycler_id == current_user.id,
    ).first()

    if record and record.recycling_status in {RecyclingStatus.RECEIVED, RecyclingStatus.PROCESSING}:
        raise HTTPException(status_code=409, detail="Physical receipt has already been acknowledged.")
    if record and record.recycling_status == RecyclingStatus.RECYCLED:
        raise HTTPException(status_code=409, detail="This device has already completed recycling.")
    if record and record.chain_attempt_status in _UNCERTAIN_ATTEMPTS:
        raise HTTPException(status_code=409, detail="A recycling ledger attempt requires reconciliation; receipt cannot be reset.")

    try:
        now_utc = datetime.now(timezone.utc)
        if record is None:
            record = RecyclingRecord(
                device_id=device.id,
                recycler_id=current_user.id,
                recycling_status=RecyclingStatus.RECEIVED,
                received_date=now_utc,
            )
            db.add(record)
        else:
            record.recycling_status = RecyclingStatus.RECEIVED
            record.received_date = now_utc
        db.commit()
        db.refresh(record)
        return standard_response({
            "device_id": device.public_id,
            "logistical_status": _enum_value(record.recycling_status),
            "message": "Physical receipt logged. Ready for recycling processing.",
        })
    except Exception as exc:
        db.rollback()
        logger.error("Could not record recycling receipt (exception_type=%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Could not record physical receipt.") from None


@router.post("/complete", status_code=status.HTTP_200_OK)
def complete_recycling(
    payload: CompleteRecyclingPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.RECYCLER])),
):
    """Complete recycling only after receipt, with durable chain-attempt recovery state."""
    device = db.query(Device).filter(Device.public_id == payload.device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found.")
    require_confirmed_v2_device(device, "recycling completion")

    if device.current_owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the recycler who currently owns this device can complete recycling.")
    if device.status != DeviceStatus.TRANSFERRED:
        raise HTTPException(
            status_code=409,
            detail=f"Device must be in TRANSFERRED state to be recycled. Current state: {_enum_value(device.status)}",
        )

    record = db.query(RecyclingRecord).filter(
        RecyclingRecord.device_id == device.id,
        RecyclingRecord.recycler_id == current_user.id,
    ).first()
    if record is None or record.recycling_status != RecyclingStatus.RECEIVED:
        raise HTTPException(status_code=409, detail="Acknowledge physical receipt before completing recycling.")
    assert_active_role_evidence(
        current_user, EvidenceType.RECYCLER_AUTHORIZATION, db, transition_account_on_failure=True
    )
    if record.chain_attempt_status in _UNCERTAIN_ATTEMPTS:
        raise HTTPException(status_code=409, detail="A previous ledger attempt requires reconciliation. Do not retry recycling automatically.")

    # Read-only chain preflight happens before setting the submission latch.
    try:
        if not ledger_service.can_recycle(device.public_id):
            raise HTTPException(
                status_code=409,
                detail="Chain pre-check failed: device must be in TRANSFERRED state on-chain.",
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Recycling chain preflight failed (exception_type=%s)", type(exc).__name__)
        raise HTTPException(status_code=502, detail="Could not confirm whether the device is eligible for recycling on-chain.") from None

    attempted_at = datetime.now(timezone.utc)
    record.chain_attempt_status = "SUBMISSION_STARTED"
    record.chain_attempted_at = attempted_at
    record.chain_error_code = None
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Could not record the recycling attempt; no chain call was made.") from None

    try:
        chain_result = ledger_service.mark_recycled(public_id=device.public_id)
    except LedgerSubmissionError as exc:
        db.rollback()
        record = db.query(RecyclingRecord).filter(RecyclingRecord.id == record.id).first()
        if record:
            record.chain_attempt_status = "SUBMISSION_UNKNOWN" if exc.submission_attempted else "PRE_SUBMISSION_FAILED"
            record.chain_error_code = exc.error_code
            try:
                db.commit()
            except Exception:
                db.rollback()
        if exc.submission_attempted:
            raise HTTPException(status_code=409, detail="Recycling outcome is uncertain. Manual reconciliation is required; do not retry.") from None
        raise HTTPException(status_code=502, detail="The recycling transaction was not submitted. Review the attempt before retrying.") from None
    except Exception as exc:
        db.rollback()
        record = db.query(RecyclingRecord).filter(RecyclingRecord.id == record.id).first()
        if record:
            record.chain_attempt_status = "SUBMISSION_UNKNOWN"
            record.chain_error_code = "UNCLASSIFIED_LEDGER_EXCEPTION"
            try:
                db.commit()
            except Exception:
                db.rollback()
        logger.warning("Recycling submission outcome uncertain (exception_type=%s)", type(exc).__name__)
        raise HTTPException(status_code=409, detail="Recycling outcome is uncertain. Manual reconciliation is required; do not retry.") from None

    # Persist chain confirmation separately before local terminal state update.
    record.chain_attempt_status = "CONFIRMED_DB_PENDING"
    record.chain_tx_id = chain_result.get("tx_id")
    record.chain_confirmed_round = chain_result.get("confirmed_round")
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=409, detail="Recycling was confirmed on-chain but local confirmation metadata could not be committed. Manual reconciliation is required.") from None

    certificate_id = f"TRC-REC-{uuid.uuid4().hex[:12].upper()}"
    try:
        device.status = DeviceStatus.RECYCLED
        device.stamp_valid = False
        device.is_for_sale = False
        record.recycling_status = RecyclingStatus.RECYCLED
        record.certificate_id = certificate_id
        record.completion_date = datetime.now(timezone.utc)
        record.chain_attempt_status = "COMPLETED"
        # Notify previous owners and verifiers who participated in this device's
        # completed handoffs. The recycler performed the action and is not sent a
        # redundant self-notification.
        completed_transfers = db.query(Transfer).filter(
            Transfer.device_id == device.id,
            Transfer.status == TransferStatus.COMPLETED,
        ).all()
        recipients = set()
        for prior_transfer in completed_transfers:
            recipients.update(uid for uid in (prior_transfer.from_user_id, prior_transfer.to_user_id) if uid)
            if prior_transfer.verifier_id:
                recipients.add(prior_transfer.verifier_id)
        recipients.discard(current_user.id)
        for recipient_id in recipients:
            create_notification(
                db,
                user_id=recipient_id,
                notification_type="device_recycled",
                title="Device reached end of life",
                message=f"{device.brand_name} · {device.public_id} has a recorded recycling-completion event. The TraceLoop record is not a government-issued recycling certificate.",
                action_path="/track?id=" + device.public_id,
                public_device_id=device.public_id,
                entity_id=record.id,
                dedupe_key=f"device:{device.id}:recycled:{recipient_id}",
            )
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=409, detail="Recycling was confirmed on-chain but the local lifecycle update failed. Manual reconciliation is required; do not retry.") from None

    # Reward distribution is a best-effort follow-up; it must not undo confirmed recycling.
    try:
        transfers = db.query(Transfer).filter(
            Transfer.device_id == device.id,
            Transfer.status == TransferStatus.COMPLETED,
        ).order_by(Transfer.completed_at.asc()).all()
        actor_ids = []
        seen = set()

        def add_actor(uid):
            if uid and uid not in seen:
                actor_ids.append(uid)
                seen.add(uid)

        if transfers:
            add_actor(transfers[0].from_user_id)
        for transfer in transfers:
            add_actor(transfer.to_user_id)
            add_actor(transfer.verifier_id)
        add_actor(current_user.id)

        if actor_ids:
            points_each = max(1, 100 // len(actor_ids))
            for uid in actor_ids:
                db.query(User).filter(User.id == uid).update(
                    {"reward_points": User.reward_points + points_each},
                    synchronize_session=False,
                )
                db.add(RewardPoint(user_id=uid, points=points_each, action="DEVICE_RECYCLED", device_id=device.id))
            db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("Reward distribution failed after recycling confirmation (exception_type=%s)", type(exc).__name__)

    return standard_response({
        "device_id": device.public_id,
        "status": "RECYCLED",
        "certificate_id": certificate_id,
        "chain_tx_id": record.chain_tx_id,
        "chain_confirmed_round": record.chain_confirmed_round,
        "message": "Device marked RECYCLED on Algorand. TraceLoop recycling record generated; this is not a CPCB-issued certificate.",
    })
