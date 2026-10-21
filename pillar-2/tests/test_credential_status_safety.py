"""Regression tests for non-destructive reads and intentional KYC transitions."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import (
    Base,
    EvidenceRecord,
    EvidenceSubjectType,
    EvidenceType,
    EvidenceVerificationStatus,
    KYCDocument,
    Notification,
    User,
    UserRole,
    UserStatus,
)
from routes import evidence_routes


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        bind=engine,
        tables=[User.__table__, KYCDocument.__table__, EvidenceRecord.__table__, Notification.__table__],
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return engine, factory()


def _active_verifier(db):
    user = User(
        id="verifier-1",
        phone="+15550001001",
        name="Service Centre",
        role=UserRole.VERIFIABLE,
        status=UserStatus.ACTIVE,
        admin_approved=True,
        brand_auth_code="AUTH-123",
        service_center_id="SC-01",
    )
    db.add(user)
    db.commit()
    return user


def test_credential_check_is_read_only_by_default_when_evidence_missing():
    engine, db = _session()
    try:
        user = _active_verifier(db)
        with pytest.raises(HTTPException) as caught:
            evidence_routes.assert_active_role_evidence(
                user, EvidenceType.BRAND_AUTHORIZATION, db
            )
        assert caught.value.status_code == 403
        db.refresh(user)
        assert user.status == UserStatus.ACTIVE
        assert user.admin_approved is True
        assert db.query(Notification).filter(Notification.user_id == user.id).count() == 0
    finally:
        db.close()
        engine.dispose()


def test_protected_action_credential_failure_explicitly_moves_active_user_to_kyc():
    engine, db = _session()
    try:
        user = _active_verifier(db)
        with pytest.raises(HTTPException) as caught:
            evidence_routes.assert_active_role_evidence(
                user,
                EvidenceType.BRAND_AUTHORIZATION,
                db,
                transition_account_on_failure=True,
            )
        assert caught.value.status_code == 403
        db.refresh(user)
        assert user.status == UserStatus.KYC_IN_PROGRESS
        assert user.admin_approved is False
        notification = db.query(Notification).filter(Notification.user_id == user.id).one()
        assert notification.notification_type == "credential_review_required"
        assert notification.action_path == "/onboarding"
    finally:
        db.close()
        engine.dispose()


def test_expiry_future_policy_does_not_treat_absent_rows_as_expiry(monkeypatch):
    engine, db = _session()
    try:
        user = _active_verifier(db)
        monkeypatch.setattr(evidence_routes, "TIME_BOUND_ENFORCEMENT_ENABLED", True)
        changed = evidence_routes.sync_expired_evidence_for_user(user, db)
        db.refresh(user)
        assert changed is False
        assert user.status == UserStatus.ACTIVE
        assert user.admin_approved is True
        assert db.query(Notification).filter(Notification.user_id == user.id).count() == 0
    finally:
        db.close()
        engine.dispose()


def test_confirmed_expiry_only_transitions_when_prior_verified_credential_expires(monkeypatch):
    engine, db = _session()
    try:
        user = _active_verifier(db)
        db.add(EvidenceRecord(
            id="EV-EXPIRED",
            user_id=user.id,
            evidence_type=EvidenceType.BRAND_AUTHORIZATION,
            subject_type=EvidenceSubjectType.USER_ROLE_CREDENTIAL,
            subject_id=user.id,
            file_reference="users/verifier-1/expired.pdf",
            original_filename="authorization.pdf",
            mime_type="application/pdf",
            file_size_bytes=100,
            file_sha256="a" * 64,
            verification_status=EvidenceVerificationStatus.VERIFIED,
            reviewed_at=datetime.now(timezone.utc) - timedelta(days=300),
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        ))
        db.commit()
        monkeypatch.setattr(evidence_routes, "TIME_BOUND_ENFORCEMENT_ENABLED", True)
        monkeypatch.setattr(evidence_routes, "TIME_BOUND_CREDENTIALS", {EvidenceType.BRAND_AUTHORIZATION})
        changed = evidence_routes.sync_expired_evidence_for_user(user, db)
        db.refresh(user)
        assert changed is True
        assert user.status == UserStatus.KYC_IN_PROGRESS
        assert user.admin_approved is False
        notification = db.query(Notification).filter(Notification.user_id == user.id).one()
        assert notification.notification_type == "credential_expired"
    finally:
        db.close()
        engine.dispose()


def _add_verified_credential(db, user):
    evidence = EvidenceRecord(
        id="EV-VERIFIED-001",
        user_id=user.id,
        evidence_type=EvidenceType.BRAND_AUTHORIZATION,
        subject_type=EvidenceSubjectType.USER_ROLE_CREDENTIAL,
        subject_id=user.id,
        file_reference="users/verifier-1/authorization.pdf",
        original_filename="authorization.pdf",
        mime_type="application/pdf",
        file_size_bytes=100,
        file_sha256="b" * 64,
        verification_status=EvidenceVerificationStatus.VERIFIED,
        reviewed_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db.add(evidence)
    db.commit()
    return evidence


def test_passive_credential_read_does_not_touch_storage_or_account_state(monkeypatch):
    engine, db = _session()
    try:
        user = _active_verifier(db)
        _add_verified_credential(db, user)

        def unexpected_storage_call(_evidence):
            raise AssertionError("passive credential check must not access object storage")

        monkeypatch.setattr(evidence_routes, "verify_evidence_file_integrity", unexpected_storage_call)
        record = evidence_routes.assert_active_role_evidence(
            user, EvidenceType.BRAND_AUTHORIZATION, db, transition_account_on_failure=False
        )
        assert record.id == "EV-VERIFIED-001"
        db.refresh(user)
        assert user.status == UserStatus.ACTIVE
        assert user.admin_approved is True
        assert db.query(Notification).filter(Notification.user_id == user.id).count() == 0
    finally:
        db.close()
        engine.dispose()


def test_transient_storage_failure_blocks_protected_action_without_downgrading(monkeypatch):
    engine, db = _session()
    try:
        user = _active_verifier(db)
        _add_verified_credential(db, user)

        def storage_unavailable(_evidence):
            raise HTTPException(status_code=502, detail="Private storage is temporarily unavailable.")

        monkeypatch.setattr(evidence_routes, "verify_evidence_file_integrity", storage_unavailable)
        with pytest.raises(HTTPException) as caught:
            evidence_routes.assert_active_role_evidence(
                user,
                EvidenceType.BRAND_AUTHORIZATION,
                db,
                transition_account_on_failure=True,
            )
        assert caught.value.status_code == 502
        db.refresh(user)
        assert user.status == UserStatus.ACTIVE
        assert user.admin_approved is True
        assert db.query(Notification).filter(Notification.user_id == user.id).count() == 0
    finally:
        db.close()
        engine.dispose()


def test_confirmed_missing_credential_file_pauses_active_account(monkeypatch):
    engine, db = _session()
    try:
        user = _active_verifier(db)
        _add_verified_credential(db, user)

        def missing_file(_evidence):
            raise HTTPException(status_code=404, detail="Stored evidence is missing.")

        monkeypatch.setattr(evidence_routes, "verify_evidence_file_integrity", missing_file)
        with pytest.raises(HTTPException) as caught:
            evidence_routes.assert_active_role_evidence(
                user,
                EvidenceType.BRAND_AUTHORIZATION,
                db,
                transition_account_on_failure=True,
            )
        assert caught.value.status_code == 403
        db.refresh(user)
        assert user.status == UserStatus.KYC_IN_PROGRESS
        assert user.admin_approved is False
        notification = db.query(Notification).filter(Notification.user_id == user.id).one()
        assert notification.notification_type == "credential_review_required"
    finally:
        db.close()
        engine.dispose()


def test_replacement_upload_with_confirmed_missing_existing_file_requires_reapproval(monkeypatch):
    engine, db = _session()
    try:
        user = _active_verifier(db)
        _add_verified_credential(db, user)

        def missing_file(_evidence):
            raise HTTPException(status_code=404, detail="Stored evidence is missing.")

        monkeypatch.setattr(evidence_routes, "verify_evidence_file_integrity", missing_file)
        evidence_routes._check_existing_role_credential_before_replacement_upload(
            user, EvidenceType.BRAND_AUTHORIZATION, db
        )
        db.refresh(user)
        assert user.status == UserStatus.KYC_IN_PROGRESS
        assert user.admin_approved is False
        notification = db.query(Notification).filter(Notification.user_id == user.id).one()
        assert notification.notification_type == "credential_review_required"
    finally:
        db.close()
        engine.dispose()


def test_replacement_upload_storage_outage_does_not_downgrade_active_account(monkeypatch):
    engine, db = _session()
    try:
        user = _active_verifier(db)
        _add_verified_credential(db, user)

        def storage_unavailable(_evidence):
            raise HTTPException(status_code=502, detail="Storage temporarily unavailable.")

        monkeypatch.setattr(evidence_routes, "verify_evidence_file_integrity", storage_unavailable)
        evidence_routes._check_existing_role_credential_before_replacement_upload(
            user, EvidenceType.BRAND_AUTHORIZATION, db
        )
        db.refresh(user)
        assert user.status == UserStatus.ACTIVE
        assert user.admin_approved is True
        assert db.query(Notification).filter(Notification.user_id == user.id).count() == 0
    finally:
        db.close()
        engine.dispose()


def test_read_only_file_presence_diagnostic_does_not_change_status(monkeypatch):
    engine, db = _session()
    try:
        user = _active_verifier(db)
        evidence = _add_verified_credential(db, user)

        monkeypatch.setattr(evidence_routes, "evidence_object_exists", lambda _reference: False)
        file_status, issue = evidence_routes.evidence_file_presence_status(evidence)
        assert file_status == "MISSING"
        assert issue
        db.refresh(user)
        assert user.status == UserStatus.ACTIVE
        assert user.admin_approved is True
    finally:
        db.close()
        engine.dispose()
