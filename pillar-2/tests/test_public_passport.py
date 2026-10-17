"""Regression tests for public passports and V1/V2 identity separation.

These tests call the endpoint function with an isolated SQLite database and a
mock ledger service. They verify application behavior without submitting any
transactions or requiring network access.
"""
from __future__ import annotations

import importlib
import sys
import types
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import database
from database import (
    Base,
    ChainStatus,
    Device,
    DeviceStatus,
    IdentityVersion,
    LedgerEvent,
    RegistrationStatus,
    User,
    UserRole,
    UserStatus,
)
from services.device_policy import require_confirmed_v2_device


class LedgerSpy:
    def __init__(self):
        self.legacy_calls: list[str] = []
        self.v2_calls: list[str] = []
        self.legacy_state = {
            "state_numeric": 1,
            "state_label": "REGISTERED",
            "owner_hash": "DO_NOT_EXPOSE_OWNER_HASH",
            "config_hash": "DO_NOT_EXPOSE_CONFIG_HASH",
            "stamp_valid": False,
            "last_verifier": "DO_NOT_EXPOSE_VERIFIER",
            "updated_at": 123,
        }
        self.v2_state = dict(self.legacy_state, state_label="REGISTERED", stamp_valid=True)

    def get_legacy_device_state(self, chain_id: str):
        self.legacy_calls.append(chain_id)
        return dict(self.legacy_state)

    def get_device_state(self, public_id: str):
        self.v2_calls.append(public_id)
        return dict(self.v2_state)


@pytest.fixture

def passport_env(monkeypatch):
    """Load the route with a fake ledger service and an isolated in-memory DB."""
    spy = LedgerSpy()
    fake_ledger_module = types.ModuleType("services.ledger_service")

    class LedgerSubmissionError(Exception):
        def __init__(self, *args, submission_attempted=False, error_code="TEST"):
            super().__init__(*args)
            self.submission_attempted = submission_attempted
            self.error_code = error_code

    fake_ledger_module.LedgerSubmissionError = LedgerSubmissionError
    fake_ledger_module.ledger_service = spy
    monkeypatch.setitem(sys.modules, "services.ledger_service", fake_ledger_module)
    # Ensure the route binds to the test double, not a stale imported module.
    sys.modules.pop("routes.device_routes", None)
    route_module = importlib.import_module("routes.device_routes")

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = TestSession()

    owner_id = str(uuid.uuid4())
    db.add(User(
        id=owner_id,
        phone="+919999000001",
        role=UserRole.FIRST_BUYER,
        status=UserStatus.ACTIVE,
        name="Test Owner",
    ))
    db.commit()

    try:
        yield {
            "db": db,
            "owner_id": owner_id,
            "ledger": spy,
            "get_public_passport": route_module.get_public_passport,
        }
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        sys.modules.pop("routes.device_routes", None)


def make_device(*, owner_id: str, identity_version: IdentityVersion,
                registration_status: RegistrationStatus, public_id: str,
                device_id: str | None = None, status: DeviceStatus = DeviceStatus.REGISTERED,
                legacy_chain_id: str | None = None, serial: str = "SENSITIVE_SERIAL") -> Device:
    return Device(
        id=device_id or str(uuid.uuid4()),
        public_id=public_id,
        identity_version=identity_version,
        legacy_chain_id=legacy_chain_id,
        registration_status=registration_status,
        registration_error_code=("PHASE2_TEST_IDENTITY_RESET" if identity_version == IdentityVersion.V1_LEGACY else None),
        serial_hash=("a" if identity_version == IdentityVersion.V1_LEGACY else "b") * 64,
        serial_raw=serial,
        brand_code="AP" if public_id.startswith("TL-APPLE-") else "DL",
        brand_name="Apple" if public_id.startswith("TL-APPLE-") else "Dell",
        original_config={"processor": "Intel Core"},
        current_config={
            "processor": "Intel Core",
            "ram": "16GB",
            "serial_number": serial,
            "nested": {"storage": "512GB", "serial": "HIDE_ME"},
        },
        current_owner_id=owner_id,
        current_owner_hash="c" * 64,
        status=status,
        stamp_valid=False,
        registered_at=datetime(2026, 10, 17, tzinfo=timezone.utc),
    )


def add_event(db, *, device_id: str, event_type: str, event_id: str):
    db.add(LedgerEvent(
        id=event_id,
        device_id=device_id,
        event_type=event_type,
        actor_type="user",
        tx_hash=f"tx-{event_id}",
        block_number=100,
        chain_status=ChainStatus.CONFIRMED,
        metadata_={"serial": "HIDE_ME", "comment": "PRIVATE_DISPUTE_COMMENT"},
    ))


def test_legacy_public_passport_uses_historical_chain_id_and_hides_private_data(passport_env):
    db = passport_env["db"]
    synthetic_public_id = "TL-APPLE-AAAAAAAAAAAAAAAAAAAAAAAA"
    historical_chain_id = "TL-AP-FAKE-SERIAL-001"
    device = make_device(
        owner_id=passport_env["owner_id"],
        identity_version=IdentityVersion.V1_LEGACY,
        registration_status=RegistrationStatus.LEGACY_TEST,
        public_id=synthetic_public_id,
        device_id=historical_chain_id,
        legacy_chain_id=historical_chain_id,
        serial="FAKE_SERIAL_001",
        status=DeviceStatus.TRANSFERRED,
    )
    db.add(device)
    db.flush()
    for i, event_type in enumerate(("REGISTERED", "VERIFIED", "TRANSFERRED", "DISPUTED", "DISPUTE_RESOLVED")):
        add_event(db, device_id=device.id, event_type=event_type, event_id=f"legacy-{i}")
    db.commit()

    response = passport_env["get_public_passport"](synthetic_public_id, db)
    data = response["data"]

    assert data["identity_version"] == "V1_LEGACY"
    assert data["is_legacy_test"] is True
    assert data["read_only"] is True
    assert data["status"] == "TRANSFERRED"
    assert data["registration_status"] == "LEGACY_TEST"
    assert data["public_id"] == synthetic_public_id
    assert passport_env["ledger"].legacy_calls == [historical_chain_id]
    assert passport_env["ledger"].v2_calls == []
    assert {event["event_type"] for event in data["events"]} == {"REGISTERED", "VERIFIED", "TRANSFERRED"}
    assert "DISPUTED" not in str(data)
    assert "DISPUTE_RESOLVED" not in str(data)
    assert "PRIVATE_DISPUTE_COMMENT" not in str(data)
    assert "FAKE_SERIAL_001" not in str(data)
    assert historical_chain_id not in str(data)
    assert "DO_NOT_EXPOSE_OWNER_HASH" not in str(data)
    assert "DO_NOT_EXPOSE_CONFIG_HASH" not in str(data)
    assert "DO_NOT_EXPOSE_VERIFIER" not in str(data)
    assert "HIDE_ME" not in str(data)
    assert data["current_config"] == {
        "processor": "Intel Core",
        "ram": "16GB",
    }


def test_v2_public_passport_queries_chain_by_public_id(passport_env):
    db = passport_env["db"]
    public_id = "TL-DELL-BBBBBBBBBBBBBBBBBBBBBBBB"
    device = make_device(
        owner_id=passport_env["owner_id"],
        identity_version=IdentityVersion.V2_PUBLIC_ID,
        registration_status=RegistrationStatus.CONFIRMED,
        public_id=public_id,
        serial="REAL_SERIAL_002",
        status=DeviceStatus.REGISTERED,
    )
    db.add(device)
    db.commit()

    response = passport_env["get_public_passport"](public_id, db)
    data = response["data"]

    assert data["identity_version"] == "V2_PUBLIC_ID"
    assert data["is_legacy_test"] is False
    assert data["read_only"] is False
    assert passport_env["ledger"].v2_calls == [public_id]
    assert passport_env["ledger"].legacy_calls == []
    assert "REAL_SERIAL_002" not in str(data)
    assert "HIDE_ME" not in str(data)


def test_public_passport_does_not_reveal_disputed_status(passport_env):
    db = passport_env["db"]
    public_id = "TL-DELL-CCCCCCCCCCCCCCCCCCCCCCCC"
    device = make_device(
        owner_id=passport_env["owner_id"],
        identity_version=IdentityVersion.V2_PUBLIC_ID,
        registration_status=RegistrationStatus.CONFIRMED,
        public_id=public_id,
        status=DeviceStatus.DISPUTED,
    )
    db.add(device)
    db.commit()
    passport_env["ledger"].v2_state["state_label"] = "DISPUTED"

    data = passport_env["get_public_passport"](public_id, db)["data"]
    assert data["status"] is None
    assert data["chain_status"] is None
    assert "DISPUTED" not in str(data)


def test_unconfirmed_v2_passport_is_not_public(passport_env):
    db = passport_env["db"]
    public_id = "TL-DELL-DDDDDDDDDDDDDDDDDDDDDDDD"
    device = make_device(
        owner_id=passport_env["owner_id"],
        identity_version=IdentityVersion.V2_PUBLIC_ID,
        registration_status=RegistrationStatus.PENDING,
        public_id=public_id,
    )
    db.add(device)
    db.commit()

    with pytest.raises(HTTPException) as exc:
        passport_env["get_public_passport"](public_id, db)
    assert exc.value.status_code == 404
    assert passport_env["ledger"].v2_calls == []


def test_only_confirmed_v2_devices_pass_lifecycle_policy(passport_env):
    owner_id = passport_env["owner_id"]
    v2 = make_device(
        owner_id=owner_id,
        identity_version=IdentityVersion.V2_PUBLIC_ID,
        registration_status=RegistrationStatus.CONFIRMED,
        public_id="TL-DELL-EEEEEEEEEEEEEEEEEEEEEEEE",
    )
    require_confirmed_v2_device(v2, "test action")

    legacy = make_device(
        owner_id=owner_id,
        identity_version=IdentityVersion.V1_LEGACY,
        registration_status=RegistrationStatus.LEGACY_TEST,
        public_id="TL-DELL-FFFFFFFFFFFFFFFFFFFFFFFF",
        device_id="TL-DL-FAKE",
        legacy_chain_id="TL-DL-FAKE",
    )
    with pytest.raises(HTTPException) as exc:
        require_confirmed_v2_device(legacy, "transfer")
    assert exc.value.status_code == 409

    pending = make_device(
        owner_id=owner_id,
        identity_version=IdentityVersion.V2_PUBLIC_ID,
        registration_status=RegistrationStatus.PENDING,
        public_id="TL-DELL-111111111111111111111111",
    )
    with pytest.raises(HTTPException) as exc:
        require_confirmed_v2_device(pending, "transfer")
    assert exc.value.status_code == 409
