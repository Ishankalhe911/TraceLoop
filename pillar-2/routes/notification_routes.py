"""Authenticated notification centre API for newly completed workflow events."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from auth import get_current_user_any_status
from database import Notification, User, get_db

router = APIRouter(prefix="/api/v1/notifications", tags=["Notifications"])


def _iso(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _item(row: Notification):
    return {
        "id": row.id,
        "type": row.notification_type,
        "title": row.title,
        "message": row.message,
        "action_path": row.action_path,
        "public_device_id": row.public_device_id,
        "entity_id": row.entity_id,
        "created_at": _iso(row.created_at),
        "read_at": _iso(row.read_at),
    }


def _response(data, code=200):
    return {"status": "success", "code": code, "data": data, "error": None, "traceloop_version": "v1"}


@router.get("")
def list_notifications(
    limit: int = Query(40, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_any_status),
):
    """Return newest notifications for this user only, plus an unread count."""
    unread_count = db.query(Notification).filter(
        Notification.user_id == current_user.id,
        Notification.read_at.is_(None),
    ).count()
    rows = db.query(Notification).filter(
        Notification.user_id == current_user.id,
    ).order_by(desc(Notification.created_at), desc(Notification.id)).limit(limit).all()
    return _response({"items": [_item(row) for row in rows], "unread_count": unread_count})


@router.post("/{notification_id}/read")
def mark_notification_read(
    notification_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_any_status),
):
    row = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.user_id == current_user.id,
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Notification not found.")
    if row.read_at is None:
        row.read_at = datetime.now(timezone.utc)
        db.commit()
    return _response({"notification": _item(row)})


@router.post("/read-all")
def mark_all_notifications_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_any_status),
):
    now = datetime.now(timezone.utc)
    changed = db.query(Notification).filter(
        Notification.user_id == current_user.id,
        Notification.read_at.is_(None),
    ).update({Notification.read_at: now}, synchronize_session=False)
    db.commit()
    return _response({"marked_read": int(changed), "read_at": _iso(now)})
