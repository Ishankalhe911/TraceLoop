from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth import get_current_user  # import keeps route auth dependencies loaded as in production
from database import Base, Notification, User, UserRole, UserStatus
from routes.notification_routes import list_notifications, mark_all_notifications_read, mark_notification_read
from services.notification_service import create_notification
from fastapi import HTTPException


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[User.__table__, Notification.__table__])
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return engine, factory()


def test_notifications_are_persisted_deduplicated_and_user_scoped():
    engine, db = _session()
    try:
        owner = User(id="owner-1", phone="+15550000001", role=UserRole.FIRST_BUYER, status=UserStatus.ACTIVE, name="Owner")
        other = User(id="owner-2", phone="+15550000002", role=UserRole.BUYER, status=UserStatus.ACTIVE, name="Other")
        db.add_all([owner, other])
        db.commit()

        kwargs = dict(
            user_id=owner.id,
            notification_type="transfer_completed",
            title="Ownership transfer completed",
            message="A device handoff has completed.",
            action_path="/track?id=TL-DELL-REAL",
            public_device_id="TL-DELL-REAL",
            entity_id="transfer-1",
            dedupe_key="transfer:transfer-1:completed:owner-1",
        )
        create_notification(db, **kwargs)
        db.commit()
        create_notification(db, **kwargs)
        db.commit()

        payload = list_notifications(limit=40, db=db, current_user=owner)
        result = payload["data"]
        assert result["unread_count"] == 1
        assert len(result["items"]) == 1
        item = result["items"][0]
        assert item["title"] == "Ownership transfer completed"
        assert item["action_path"] == "/track?id=TL-DELL-REAL"
        assert item["read_at"] is None

        try:
            mark_notification_read(item["id"], db=db, current_user=other)
            raise AssertionError("A different user should not be able to mark this notification read")
        except HTTPException as exc:
            assert exc.status_code == 404

        mark_notification_read(item["id"], db=db, current_user=owner)
        assert db.query(Notification).filter(Notification.user_id == owner.id, Notification.read_at.is_(None)).count() == 0

        create_notification(
            db,
            user_id=owner.id,
            notification_type="verification_completed",
            title="Device verification completed",
            message="A verification stamp was recorded.",
            public_device_id="TL-DELL-REAL",
            dedupe_key="verification:req-2:stamped:owner-1",
        )
        db.commit()
        marked = mark_all_notifications_read(db=db, current_user=owner)
        assert marked["data"]["marked_read"] == 1
        assert db.query(Notification).filter(Notification.user_id == owner.id, Notification.read_at.is_(None)).count() == 0
    finally:
        db.close()
        engine.dispose()
