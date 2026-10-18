"""Public route eligibility tests for deterministic passport intelligence."""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from database import Device, DeviceStatus, IdentityVersion, RegistrationStatus
from routes import passport_intelligence_routes as routes


PUBLIC_ID = "TL-DELL-AAAAAAAAAAAAAAAAAAAAAAAA"


class FakeQuery:
    def __init__(self, value):
        self.value = value

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.value


class FakeDB:
    def __init__(self, device):
        self.device = device

    def query(self, _model):
        return FakeQuery(self.device)


def make_device(*, registration_status=RegistrationStatus.CONFIRMED,
                identity_version=IdentityVersion.V2_PUBLIC_ID, public_id=PUBLIC_ID):
    return Device(
        id=str(uuid.uuid4()),
        public_id=public_id,
        identity_version=identity_version,
        legacy_chain_id=None,
        registration_status=registration_status,
        serial_hash="a" * 64,
        serial_raw="PRIVATE-SERIAL",
        brand_code="DL",
        brand_name="Dell",
        original_config={},
        current_config={},
        current_owner_id=str(uuid.uuid4()),
        current_owner_hash="b" * 64,
        status=DeviceStatus.REGISTERED,
        stamp_valid=False,
        registered_at=datetime(2026, 10, 18, tzinfo=timezone.utc),
    )


def test_invalid_public_id_is_rejected_without_database_lookup():
    class ExplodingDB:
        def query(self, _model):
            raise AssertionError("Invalid IDs should be rejected before lookup")

    with pytest.raises(HTTPException) as exc:
        routes.get_passport_intelligence("TL-DL-RAW-SERIAL", ExplodingDB())
    assert exc.value.status_code == 404


def test_pending_v2_record_is_not_exposed(monkeypatch):
    device = make_device(registration_status=RegistrationStatus.PENDING)

    class MustNotBuildFacts:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("Facts must not be built for a non-public pending record")

    monkeypatch.setattr(routes, "PassportFactsService", MustNotBuildFacts)
    with pytest.raises(HTTPException) as exc:
        routes.get_passport_intelligence(PUBLIC_ID, FakeDB(device))
    assert exc.value.status_code == 404


def test_response_uses_only_facts_payload_and_never_appends_lookup_id(monkeypatch):
    device = make_device()

    class DummyFacts:
        def __init__(self, _db):
            pass

        def build(self, resolved_device):
            assert resolved_device is device
            return {"summary": "A deterministic summary.", "reconciliation": {"status": "MATCH"}}

    monkeypatch.setattr(routes, "PassportFactsService", DummyFacts)
    response = routes.get_passport_intelligence(PUBLIC_ID, FakeDB(device))
    assert response["data"]["summary"] == "A deterministic summary."
    assert "public_id" not in response["data"]
    assert device.id not in str(response)
    assert PUBLIC_ID not in str(response)
