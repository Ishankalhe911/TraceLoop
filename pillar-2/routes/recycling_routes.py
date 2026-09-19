import uuid
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database import (
    get_db, Device, DeviceStatus, User, UserRole,
    RecyclingRecord, RecyclingStatus, Transfer, TransferStatus,
    RewardPoint
)
from auth import RequireRole, get_current_user
from services.ledger_service import ledger_service

router = APIRouter(prefix="/recycling", tags=["Recycling"])
logger = logging.getLogger("TraceLoop.Recycling")

def standard_response(data: dict = None, code: int = 200) -> dict:
    return {
        "status": "success",
        "code": code,
        "data": data or {},
        "error": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "traceloop_version": "v1"
    }

class ReceiveDevicePayload(BaseModel):
    device_id: str
    condition_notes: str = None

class CompleteRecyclingPayload(BaseModel):
    device_id: str


@router.post("/receive", status_code=status.HTTP_200_OK)
def receive_device(
    payload: ReceiveDevicePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.RECYCLER]))
):
    """
    Step 1: Recycler acknowledges physical receipt.
    Purely off-chain logistical tracking — no blockchain call.
    """
    device = db.query(Device).filter(Device.id == payload.device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found.")

    if device.current_owner_id != current_user.id or device.status != DeviceStatus.TRANSFERRED:
        raise HTTPException(
            status_code=403,
            detail="Device has not been officially transferred to your facility via the verification network."
        )

    try:
        record = db.query(RecyclingRecord).filter(
            RecyclingRecord.device_id == device.id,
            RecyclingRecord.recycler_id == current_user.id
        ).first()

        now_utc = datetime.now(timezone.utc)

        if not record:
            record = RecyclingRecord(
                device_id=device.id,
                recycler_id=current_user.id,
                recycling_status=RecyclingStatus.RECEIVED,
                received_date=now_utc
            )
            db.add(record)
        else:
            record.recycling_status = RecyclingStatus.RECEIVED
            record.received_date = now_utc

        db.commit()

        return standard_response({
            "device_id": device.id,
            "logistical_status": record.recycling_status,
            "message": "Physical receipt logged. Ready for recycling processing."
        })

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/complete", status_code=status.HTTP_200_OK)
def complete_recycling(
    payload: CompleteRecyclingPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.RECYCLER]))
):
    """
    Step 2: Recycler permanently terminates the device on Algorand.
    Generates CPCB-compliant certificate and distributes Eco Points.
    """
    device = db.query(Device).filter(Device.id == payload.device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found.")

    if device.status != DeviceStatus.TRANSFERRED:
        raise HTTPException(
            status_code=409,
            detail=f"Device must be in TRANSFERRED state to be recycled. Current state: {device.status}"
        )

    record = db.query(RecyclingRecord).filter(
        RecyclingRecord.device_id == device.id,
        RecyclingRecord.recycler_id == current_user.id
    ).first()

    if not record or record.recycling_status == RecyclingStatus.RECYCLED:
        raise HTTPException(status_code=400, detail="Invalid recycling record state or already recycled.")

    try:
        # FIX #6: UUID-based cert ID — no race condition
        certificate_id = f"TRC-CERT-{uuid.uuid4().hex[:8].upper()}"

        # Pre-flight chain check
        if not ledger_service.can_recycle(device.id):
            raise HTTPException(
                status_code=409,
                detail="Chain pre-check failed: device must be in TRANSFERRED state on-chain."
            )

        # Blockchain mutation (Pillar 1)
        chain_result = ledger_service.mark_recycled(device_id=device.id)

        # DB terminal state
        device.status = DeviceStatus.RECYCLED
        device.stamp_valid = False
        device.is_for_sale = False

        record.recycling_status = RecyclingStatus.RECYCLED
        record.certificate_id = certificate_id
        record.completion_date = datetime.now(timezone.utc)

        db.commit()

        # --- REWARD POINTS: Distribute 100 pts across all chain actors ---
        try:
            transfers = db.query(Transfer).filter(
                Transfer.device_id == device.id,
                Transfer.status == TransferStatus.COMPLETED
            ).order_by(Transfer.completed_at.asc()).all()

            actor_ids = []
            seen = set()

            def add_actor(uid):
                if uid and uid not in seen:
                    actor_ids.append(uid)
                    seen.add(uid)

            # First owner → all buyers → all verifiers → recycler
            if transfers:
                add_actor(transfers[0].from_user_id)
            for t in transfers:
                add_actor(t.to_user_id)
                add_actor(t.verifier_id)
            add_actor(current_user.id)

            if actor_ids:
                points_each = max(1, 100 // len(actor_ids))
                for uid in actor_ids:
                    # Increment running total on user (for dashboard badge)
                    db.query(User).filter(User.id == uid).update(
                        {"reward_points": User.reward_points + points_each},
                        synchronize_session=False
                    )
                    # Write transaction log entry (for history)
                    db.add(RewardPoint(
                        user_id=uid,
                        points=points_each,
                        action="DEVICE_RECYCLED",
                        device_id=device.id
                    ))
                db.commit()
                logger.info(
                    f"Distributed {points_each} pts each to {len(actor_ids)} "
                    f"actors for device {device.id}"
                )

        except Exception as e:
            # Non-critical — recycling already committed, never fail it for points
            logger.warning(f"Reward distribution failed for {device.id}: {e}")

        return standard_response({
            "device_id": device.id,
            "status": "RECYCLED",
            "certificate_id": certificate_id,
            "chain_tx_id": chain_result["tx_id"],
            "message": "Device terminated on Algorand. E-Waste certificate generated. Eco Points distributed."
        })

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ledger termination failed: {str(e)}"
        )