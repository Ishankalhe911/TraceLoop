"""Helpers for durable, deduplicated user lifecycle notifications."""
from __future__ import annotations

from typing import Optional
from database import Notification
from sqlalchemy.orm import Session


def create_notification(
    db: Session,
    *,
    user_id: Optional[str],
    notification_type: str,
    title: str,
    message: str,
    action_path: Optional[str] = None,
    public_device_id: Optional[str] = None,
    entity_id: Optional[str] = None,
    dedupe_key: Optional[str] = None,
) -> None:
    """Attach a notification to the caller's transaction; never commit here.

    Dedupe keys make repeated workflow handling safe. Keeping notification writes
    in the lifecycle transaction prevents a success notification when the actual
    state transition did not commit.
    """
    if not user_id:
        return
    if dedupe_key and db.query(Notification.id).filter(Notification.dedupe_key == dedupe_key).first():
        return
    db.add(Notification(
        user_id=user_id,
        notification_type=notification_type[:48],
        title=title[:140],
        message=message[:600],
        action_path=action_path[:300] if action_path else None,
        public_device_id=public_device_id[:40] if public_device_id else None,
        entity_id=entity_id[:36] if entity_id else None,
        dedupe_key=dedupe_key[:200] if dedupe_key else None,
    ))
