from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import (
    Base,
    Device,
    DeviceStatus,
    IdentityVersion,
    RegistrationStatus,
    RewardPoint,
    User,
    UserRole,
    UserStatus,
)
from routes.reward_points_routes import get_my_reward_points


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        bind=engine,
        tables=[User.__table__, Device.__table__, RewardPoint.__table__],
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return engine, factory()


def _user(user_id, phone, points):
    return User(
        id=user_id,
        phone=phone,
        name=user_id,
        role=UserRole.FIRST_BUYER,
        status=UserStatus.ACTIVE,
        reward_points=points,
    )


def test_reward_history_is_scoped_to_authenticated_user_and_keeps_existing_values():
    engine, db = _session()
    try:
        owner = _user("owner-1", "+15550000001", 25)
        other = _user("owner-2", "+15550000002", 999)
        db.add_all([owner, other])
        db.flush()
        device = Device(
            id="internal-device-id-1",
            public_id="TL-DELL-PUBLIC1",
            identity_version=IdentityVersion.V2_PUBLIC_ID,
            registration_status=RegistrationStatus.CONFIRMED,
            serial_hash="a" * 64,
            serial_raw="SN-PRIVATE-001",
            brand_code="DL",
            brand_name="Dell",
            original_config={},
            current_config={},
            current_owner_id=owner.id,
            current_owner_hash="b" * 64,
            status=DeviceStatus.RECYCLED,
        )
        db.add(device)
        db.flush()
        now = datetime.utcnow()
        db.add_all([
            RewardPoint(id="reward-older", user_id=owner.id, points=10, action="DEVICE_RECYCLED", device_id=device.id, created_at=now - timedelta(days=1)),
            RewardPoint(id="reward-newer", user_id=owner.id, points=15, action="DEVICE_RECYCLED", device_id=device.id, created_at=now),
            RewardPoint(id="reward-other", user_id=other.id, points=999, action="DEVICE_RECYCLED", device_id=device.id, created_at=now),
        ])
        db.commit()

        response = get_my_reward_points(limit=20, offset=0, db=db, current_user=owner)
        data = response["data"]
        assert data["balance_points"] == 25
        assert data["transaction_points_total"] == 25
        assert data["balance_matches_history"] is True
        assert data["transactions_count"] == 2
        assert [item["reward_id"] for item in data["items"]] == ["reward-newer", "reward-older"]
        assert data["items"][0]["public_device_id"] == "TL-DELL-PUBLIC1"
        assert "device_id" not in data["items"][0]
        assert all(item["reward_id"] != "reward-other" for item in data["items"])
    finally:
        db.close()
        engine.dispose()


def test_reward_history_reports_balance_mismatch_without_mutating_records():
    engine, db = _session()
    try:
        owner = _user("owner-1", "+15550000001", 40)
        db.add(owner)
        db.add(RewardPoint(id="reward-1", user_id=owner.id, points=20, action="V1_RECYCLE", device_id=None))
        db.commit()

        data = get_my_reward_points(limit=20, offset=0, db=db, current_user=owner)["data"]
        assert data["balance_points"] == 40
        assert data["transaction_points_total"] == 20
        assert data["balance_matches_history"] is False
        assert db.query(RewardPoint).filter(RewardPoint.user_id == owner.id).count() == 1
        assert db.query(User).filter(User.id == owner.id).one().reward_points == 40
    finally:
        db.close()
        engine.dispose()


def test_reward_history_pagination_does_not_expose_other_accounts():
    engine, db = _session()
    try:
        owner = _user("owner-1", "+15550000001", 6)
        db.add(owner)
        now = datetime.utcnow()
        db.add_all([
            RewardPoint(id=f"reward-{i}", user_id=owner.id, points=2, action="DEVICE_RECYCLED", created_at=now - timedelta(minutes=i))
            for i in range(3)
        ])
        db.commit()

        first = get_my_reward_points(limit=2, offset=0, db=db, current_user=owner)["data"]
        second = get_my_reward_points(limit=2, offset=2, db=db, current_user=owner)["data"]
        assert len(first["items"]) == 2
        assert first["has_more"] is True
        assert len(second["items"]) == 1
        assert second["has_more"] is False
        assert first["transactions_count"] == second["transactions_count"] == 3
    finally:
        db.close()
        engine.dispose()
