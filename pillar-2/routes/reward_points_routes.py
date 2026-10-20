"""Authenticated, read-only API for the current user's existing Eco Points."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, or_
from sqlalchemy.orm import Session

from auth import get_current_user_any_status
from database import Device, RewardPoint, User, get_db

router = APIRouter(prefix="/api/v1/rewards", tags=["Eco Points"])

_ACTION_LABELS = {
    "DEVICE_RECYCLED": "Device recycling completion recorded",
    "RECYCLING_COMPLETED": "Device recycling completion recorded",
    "DEVICE_RECYCLE": "Device recycling completion recorded",
    "VERIFICATION_COMPLETED": "Device verification completed",
    "DEVICE_VERIFIED": "Device verification recorded",
    "TRANSFER_COMPLETED": "Ownership transfer completed",
    "OWNERSHIP_TRANSFER_COMPLETED": "Ownership transfer completed",
}


def _iso(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _enum_value(value):
    return getattr(value, "value", value)


def _response(data, code=200):
    return {
        "status": "success",
        "code": code,
        "data": data,
        "error": None,
        "traceloop_version": "v1",
    }


@router.get("/me")
def get_my_reward_points(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_any_status),
):
    """Return this account's balance and persisted reward transactions only.

    This endpoint does not award, repair, or backfill points. The account balance
    comes from ``users.reward_points``; the history is read from ``reward_points``.
    If those V1-era sources differ, the response surfaces the mismatch instead
    of silently choosing one value or modifying historical records.
    """
    own_rows = db.query(RewardPoint).filter(RewardPoint.user_id == current_user.id)
    transactions_count = own_rows.count()
    transaction_points_total = int(
        db.query(func.coalesce(func.sum(RewardPoint.points), 0))
        .filter(RewardPoint.user_id == current_user.id)
        .scalar()
        or 0
    )
    balance_points = int(current_user.reward_points or 0)
    rows = (
        own_rows.order_by(desc(RewardPoint.created_at), desc(RewardPoint.id))
        .offset(offset)
        .limit(limit)
        .all()
    )

    # RewardPoint historically stores an internal device row ID. Resolve it to a
    # public passport ID only for V2 records; never leak internal IDs or synthetic
    # legacy test identifiers into this API response.
    device_refs = {str(row.device_id) for row in rows if row.device_id}
    public_device_by_ref = {}
    if device_refs:
        devices = db.query(Device).filter(
            or_(Device.id.in_(device_refs), Device.public_id.in_(device_refs))
        ).all()
        for device in devices:
            identity_version = _enum_value(device.identity_version)
            if identity_version == "V2_PUBLIC_ID" and device.public_id:
                public_device_by_ref[str(device.id)] = device.public_id
                public_device_by_ref[str(device.public_id)] = device.public_id

    items = []
    for row in rows:
        action = str(row.action or "UNKNOWN").strip().upper()
        items.append(
            {
                "reward_id": row.id,
                "points": int(row.points or 0),
                "action": action,
                "action_label": _ACTION_LABELS.get(action),
                "public_device_id": public_device_by_ref.get(str(row.device_id)) if row.device_id else None,
                "created_at": _iso(row.created_at),
            }
        )

    return _response(
        {
            "balance_points": balance_points,
            "transaction_points_total": transaction_points_total,
            "balance_matches_history": balance_points == transaction_points_total,
            "transactions_count": transactions_count,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(rows) < transactions_count,
            "items": items,
        }
    )
