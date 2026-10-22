from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import (
    Base,
    ChangeType,
    Device,
    DeviceHardwareLog,
    DeviceStatus,
    IdentityVersion,
    RegistrationStatus,
    RecyclingRecord,
    RecyclingStatus,
    Transfer,
    TransferStatus,
    User,
    UserRole,
    UserStatus,
    VerificationRequest,
    VerificationStatus,
)
from services.lifecycle_anomaly_service import (
    build_lifecycle_findings,
    explain_lifecycle_findings,
)


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _user(db, user_id, role):
    user = User(
        id=user_id,
        phone=f"+1555{hashlib.sha256(user_id.encode()).hexdigest()[:10]}",
        role=role,
        status=UserStatus.ACTIVE,
        name=f"Test {role.value}",
        admin_approved=True,
    )
    db.add(user)
    db.flush()
    return user


def _device(db, *, device_id="device-1", identity_version=IdentityVersion.V2_PUBLIC_ID,
            registration_status=RegistrationStatus.CONFIRMED, status=DeviceStatus.TRANSFERRED,
            stamp_valid=False, last_verified_at=None, last_verified_by=None):
    owner = _user(db, f"owner-{device_id}", UserRole.BUYER)
    device = Device(
        id=device_id,
        public_id=f"TL-DEMO-{device_id.upper()}",
        identity_version=identity_version,
        registration_status=registration_status,
        registration_tx_id=f"TX-{device_id}" if registration_status == RegistrationStatus.CONFIRMED else None,
        serial_hash=(device_id + "a") * (64 // len(device_id + "a") + 1),
        serial_raw=f"SERIAL-{device_id}",
        brand_code="DL",
        brand_name="Dell",
        original_config={"ram": "8GB"},
        current_config={"ram": "8GB"},
        current_owner_id=owner.id,
        current_owner_hash="a" * 64,
        status=status,
        stamp_valid=stamp_valid,
        last_verified_at=last_verified_at,
        last_verified_by=last_verified_by,
        registered_at=datetime.now(timezone.utc) - timedelta(days=90),
    )
    db.add(device)
    db.flush()
    return device


def test_transfer_policy_flag_is_reported_as_review_signal_not_fraud(db_session):
    device = _device(db_session)
    sender = db_session.query(User).filter(User.id == device.current_owner_id).one()
    recipient = _user(db_session, "recipient-1", UserRole.BUYER)
    db_session.add(Transfer(
        id="transfer-flagged-1",
        device_id=device.id,
        from_user_id=sender.id,
        to_user_id=recipient.id,
        assigned_verifier_id=None,
        status=TransferStatus.COMPLETED,
        velocity_flagged=True,
        count_flagged=False,
        initiated_at=datetime.now(timezone.utc) - timedelta(hours=1),
    ))
    db_session.commit()

    result = build_lifecycle_findings(db_session)
    finding = next(item for item in result["findings"] if item["category"] == "TRANSFER_POLICY_SIGNAL")
    assert finding["priority"] == "REVIEW"
    assert "not evidence of misconduct" in finding["summary"]
    assert finding["public_device_id"] == device.public_id
    assert "serial" not in str(finding).lower()


def test_repeated_hardware_changes_are_surfaced_with_existing_evidence(db_session):
    device = _device(db_session)
    actor = _user(db_session, "hardware-actor", UserRole.VERIFIABLE)
    now = datetime.now(timezone.utc)
    for index in range(3):
        db_session.add(DeviceHardwareLog(
            id=f"hw-log-{index}",
            device_id=device.id,
            component=f"component-{index}",
            change_type=ChangeType.UPGRADED,
            old_spec=f"old-{index}",
            new_spec=f"new-{index}",
            declared_by=actor.id,
            stamp_invalidated=True,
            created_at=(now - timedelta(days=index)).replace(tzinfo=None),
        ))
    db_session.commit()

    result = build_lifecycle_findings(db_session, now=now)
    finding = next(item for item in result["findings"] if item["category"] == "REPEATED_HARDWARE_CHANGES")
    assert "3 hardware-change records" in finding["summary"]
    assert "not" not in finding["review_check"].lower() or "verify" in finding["review_check"].lower()
    assert finding["source_type"] == "device_hardware_log"


def test_uncertain_transfer_and_recycling_attempts_are_action_signals(db_session):
    device = _device(db_session)
    sender = db_session.query(User).filter(User.id == device.current_owner_id).one()
    recipient = _user(db_session, "recipient-2", UserRole.BUYER)
    recycler = _user(db_session, "recycler-2", UserRole.RECYCLER)
    now = datetime.now(timezone.utc)
    db_session.add(Transfer(
        id="transfer-unknown-1", device_id=device.id, from_user_id=sender.id,
        to_user_id=recipient.id, status=TransferStatus.IN_PROGRESS,
        chain_attempt_status="SUBMISSION_UNKNOWN",
        chain_attempted_at=now - timedelta(minutes=3),
    ))
    db_session.add(RecyclingRecord(
        id="recycling-pending-1", device_id=device.id, recycler_id=recycler.id,
        recycling_status=RecyclingStatus.RECEIVED,
        chain_attempt_status="CONFIRMED_DB_PENDING",
        chain_attempted_at=now - timedelta(minutes=20),
    ))
    db_session.commit()

    result = build_lifecycle_findings(db_session, now=now)
    categories = {item["category"] for item in result["findings"]}
    assert "TRANSFER_CHAIN_RECONCILIATION" in categories
    assert "RECYCLING_CHAIN_RECONCILIATION" in categories
    action_findings = [item for item in result["findings"] if item["priority"] == "ACTION"]
    assert len(action_findings) >= 2
    assert all("Do not blindly" in item["review_check"] or "Do not retry" in item["summary"] for item in action_findings if item["category"].endswith("CHAIN_RECONCILIATION"))


def test_legacy_device_policy_flags_are_excluded(db_session):
    device = _device(
        db_session,
        device_id="legacy-device",
        identity_version=IdentityVersion.V1_LEGACY,
        registration_status=RegistrationStatus.LEGACY_TEST,
    )
    sender = db_session.query(User).filter(User.id == device.current_owner_id).one()
    recipient = _user(db_session, "recipient-3", UserRole.BUYER)
    db_session.add(Transfer(
        id="legacy-transfer-flagged", device_id=device.id,
        from_user_id=sender.id, to_user_id=recipient.id,
        status=TransferStatus.COMPLETED, velocity_flagged=True,
        initiated_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    result = build_lifecycle_findings(db_session)
    assert all(item["public_device_id"] != device.public_id for item in result["findings"])
    assert all(item["category"] != "TRANSFER_POLICY_SIGNAL" for item in result["findings"])


def test_ai_explanation_has_safe_deterministic_fallback_without_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    brief = explain_lifecycle_findings([{
        "category": "TRANSFER_CHAIN_RECONCILIATION",
        "priority": "ACTION",
        "title": "Transfer needs chain reconciliation",
        "ai_facts": {"attempt_status": "SUBMISSION_UNKNOWN", "age_minutes": 12},
    }])
    assert brief["provider"] == "deterministic_fallback"
    assert brief["generated"] is False
    assert "not conclusions" in brief["summary"]
    assert brief["recommended_checks"]


def test_hardware_change_newer_than_valid_stamp_is_flagged_for_reconciliation(db_session):
    verifier = _user(db_session, "verifier-stamp", UserRole.VERIFIABLE)
    now = datetime.now(timezone.utc)
    device = _device(
        db_session,
        device_id="stamp-hardware-device",
        status=DeviceStatus.VERIFIED,
        stamp_valid=True,
        last_verified_at=now - timedelta(days=2),
        last_verified_by=verifier.id,
    )
    db_session.add(DeviceHardwareLog(
        id="stamp-hardware-log",
        device_id=device.id,
        component="ram",
        change_type=ChangeType.UPGRADED,
        old_spec="8GB",
        new_spec="16GB",
        declared_by=verifier.id,
        stamp_invalidated=True,
        created_at=(now - timedelta(days=1)).replace(tzinfo=None),
    ))
    db_session.commit()

    result = build_lifecycle_findings(db_session, now=now)
    finding = next(item for item in result["findings"] if item["category"] == "STAMP_HARDWARE_CONSISTENCY")
    assert finding["priority"] == "ACTION"
    assert "hardware history is newer" in finding["summary"]


def test_three_stamps_in_24_hours_create_review_signal(db_session):
    owner = _user(db_session, "owner-stamps", UserRole.BUYER)
    verifier = _user(db_session, "verifier-stamps", UserRole.VERIFIABLE)
    now = datetime.now(timezone.utc)
    device = _device(
        db_session,
        device_id="stamp-cluster-device",
        status=DeviceStatus.VERIFIED,
        stamp_valid=True,
        last_verified_at=now - timedelta(minutes=5),
        last_verified_by=verifier.id,
    )
    for index, minutes in enumerate((20, 10, 5)):
        db_session.add(VerificationRequest(
            id=f"verification-cluster-{index}",
            device_id=device.id,
            requested_by=owner.id,
            verifier_id=verifier.id,
            status=VerificationStatus.STAMPED,
            stamped_at=now - timedelta(minutes=minutes),
            created_at=now - timedelta(minutes=minutes + 1),
        ))
    db_session.commit()

    result = build_lifecycle_findings(db_session, now=now)
    finding = next(item for item in result["findings"] if item["category"] == "SHORT_INTERVAL_VERIFICATIONS")
    assert "3 completed verification stamps" in finding["summary"]
    assert "not evidence" not in finding["summary"].lower()  # it explains repeated checks, not misconduct
    assert "legitimate repeated checks" in finding["summary"]


def _clear_anomaly_ai_state(monkeypatch):
    import services.lifecycle_anomaly_service as service
    with service._AI_LOCK:
        service._AI_CACHE.update({"key": None, "until": 0.0, "value": None})
        service._AI_CALLS.clear()
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("GEMINI_MODEL", "test-model")
    return service


def test_ai_explanation_uses_validated_json_and_sends_only_allowlisted_facts(monkeypatch):
    service = _clear_anomaly_ai_state(monkeypatch)
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": json.dumps({
                "summary": "The recorded signals identify lifecycle records that need a source-level review before an administrator draws a conclusion.",
                "recommended_checks": ["Compare the linked transaction outcome with the local record before attempting reconciliation."],
            })}]}}]}

    def fake_post(url, *, headers, json, timeout):
        captured["payload"] = __import__("json").dumps(json)
        return FakeResponse()

    # Keep the fake transport independent of network access; only the payload is inspected.
    monkeypatch.setattr(service.requests, "post", fake_post)
    finding = {
        "category": "TRANSFER_CHAIN_RECONCILIATION",
        "priority": "ACTION",
        "title": "Transfer needs chain reconciliation",
        "public_device_id": "TL-DEMO-PRIVATE-PUBLIC-ID",
        "source_id": "internal-transfer-uuid-secret",
        "ai_facts": {
            "attempt_status": "SUBMISSION_UNKNOWN",
            "age_minutes": 12,
            "user_supplied_text": "SHOULD_NOT_BE_SENT",
        },
    }

    brief = service.explain_lifecycle_findings([finding])

    assert brief["provider"] == "gemini"
    assert brief["generated"] is True
    assert "SHOULD_NOT_BE_SENT" not in captured["payload"]
    assert "internal-transfer-uuid-secret" not in captured["payload"]
    assert "TL-DEMO-PRIVATE-PUBLIC-ID" not in captured["payload"]
    assert "SUBMISSION_UNKNOWN" in captured["payload"]


def test_ai_unsafe_or_overreaching_model_response_falls_back(monkeypatch):
    service = _clear_anomaly_ai_state(monkeypatch)

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": json.dumps({
                "summary": "This is fraud and the account should be suspended immediately based on the detected pattern.",
                "recommended_checks": ["Suspend the account and submit another transaction immediately."],
            })}]}}]}

    monkeypatch.setattr(service.requests, "post", lambda *args, **kwargs: FakeResponse())
    brief = service.explain_lifecycle_findings([{
        "category": "TRANSFER_POLICY_SIGNAL",
        "priority": "REVIEW",
        "title": "Transfer policy signal recorded",
        "ai_facts": {"signal_codes": ["transfer_velocity"]},
    }])

    assert brief["provider"] == "deterministic_fallback"
    assert brief["generated"] is False
    assert brief["fallback_reason"] == "model output failed safety validation"
    assert "fraud" not in brief["summary"].lower()
