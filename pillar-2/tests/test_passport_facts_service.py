"""Deterministic reconciliation tests using isolated SQLite and fake chain clients."""
import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import (
    Base,
    ChainStatus,
    Device,
    DeviceStatus,
    IdentityVersion,
    LedgerEvent,
    RegistrationStatus,
)
from services.passport_facts_service import PassportFactsService


PUBLIC_ID = "TL-DELL-AAAAAAAAAAAAAAAAAAAAAAAA"
OWNER_HASH = "a" * 64
CONFIG = {"processor": "Intel Core i5", "ram": "16GB"}
TX_ID = "CONFIRMED-TX-REGISTERED"


def config_hash(config):
    compact = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(compact.encode()).hexdigest()


class FakeAlgodClient:
    def __init__(self, last_round=500100):
        self.last_round = last_round

    def status(self):
        return {"last-round": self.last_round}


class FakeLedger:
    def __init__(self, state=None, last_round=500100):
        self.state = state or {
            "state_numeric": 1,
            "state_label": "REGISTERED",
            "owner_hash": OWNER_HASH,
            "config_hash": config_hash(CONFIG),
            "stamp_valid": False,
            "last_verifier": "PRIVATE-VERIFIER-ADDRESS",
            "updated_at": 123456,
        }
        self.client = FakeAlgodClient(last_round)
        self.v2_calls = []
        self.legacy_calls = []

    def get_device_state(self, public_id):
        self.v2_calls.append(public_id)
        return dict(self.state)

    def get_legacy_device_state(self, legacy_chain_id):
        self.legacy_calls.append(legacy_chain_id)
        return dict(self.state)


class FakeHistory:
    def __init__(self, *, events=None, indexer_round=500100, complete=True, status="AVAILABLE"):
        self.events = list(events if events is not None else [{
            "event_type": "REGISTERED",
            "description": "The device was registered on the ledger.",
            "transaction_ref": TX_ID,
            "round": 500090,
            "intra_round_offset": 1,
            "log_index": 0,
            "timestamp": "2026-10-17T10:00:00+00:00",
        }])
        self.indexer_round = indexer_round
        self.complete = complete
        self.status = status
        self.calls = []

    def get_device_history(self, chain_key, *, registration_round=None):
        self.calls.append((chain_key, registration_round))
        return {
            "status": self.status,
            "complete": self.complete,
            "events": list(self.events),
            "history_from_round": registration_round or 50000000,
            "indexer_through_round": self.indexer_round,
            "transactions_scanned": 8,
            "pages_scanned": 1,
            "failure_code": None if self.complete else "HISTORY_INCOMPLETE",
            "decode_warnings": 0,
        }


def _db_and_device(*, status=DeviceStatus.REGISTERED,
                   identity_version=IdentityVersion.V2_PUBLIC_ID,
                   registration_status=RegistrationStatus.CONFIRMED,
                   public_id=PUBLIC_ID, device_id=None, legacy_chain_id=None,
                   registration_round=500090):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = TestSession()
    device = Device(
        id=device_id or str(uuid.uuid4()),
        public_id=public_id,
        identity_version=identity_version,
        legacy_chain_id=legacy_chain_id,
        registration_status=registration_status,
        registration_confirmed_round=registration_round,
        registration_tx_id=TX_ID if registration_status == RegistrationStatus.CONFIRMED else None,
        serial_hash="b" * 64,
        serial_raw="PRIVATE-SERIAL-DO-NOT-RETURN",
        brand_code="DL",
        brand_name="Dell",
        original_config=dict(CONFIG),
        current_config=dict(CONFIG),
        current_owner_id=str(uuid.uuid4()),
        current_owner_hash=OWNER_HASH,
        status=status,
        stamp_valid=False,
        registered_at=datetime(2026, 10, 17, tzinfo=timezone.utc),
    )
    db.add(device)
    db.flush()
    db.add(LedgerEvent(
        id=str(uuid.uuid4()),
        device_id=device.id,
        event_type="REGISTERED",
        actor_type="SYSTEM",
        tx_hash=TX_ID,
        block_number=500090,
        chain_status=ChainStatus.CONFIRMED,
        metadata_={"log_index": 0, "comment": "PRIVATE-COMMENT-SHOULD-NOT-RETURN"},
        timestamp=datetime(2026, 10, 17, tzinfo=timezone.utc),
    ))
    db.commit()
    return engine, db, device


def _cleanup(engine, db):
    db.close()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_matching_sources_produce_reproducible_match_and_no_private_fields():
    engine, db, device = _db_and_device()
    ledger = FakeLedger()
    history = FakeHistory()
    try:
        service = PassportFactsService(
            db, ledger_reader=ledger, history_reader=history,
            clock=lambda: datetime(2026, 10, 18, tzinfo=timezone.utc),
        )
        first = service.build(device)
        second = service.build(device)

        assert first == second
        assert first["reconciliation"]["status"] == "MATCH"
        assert {check["result"] for check in first["reconciliation"]["checks"]} == {"MATCH"}
        assert first["sources"]["independent_chain_history"] == "COMPLETE"
        assert first["timeline"][0]["transaction_ref"] == TX_ID
        serialized = str(first)
        assert device.id not in serialized
        assert device.public_id not in serialized
        assert "PRIVATE-SERIAL-DO-NOT-RETURN" not in serialized
        assert OWNER_HASH not in serialized
        assert config_hash(CONFIG) not in serialized
        assert "PRIVATE-VERIFIER-ADDRESS" not in serialized
        assert "PRIVATE-COMMENT-SHOULD-NOT-RETURN" not in serialized
        assert ledger.v2_calls == [PUBLIC_ID, PUBLIC_ID]
    finally:
        _cleanup(engine, db)


def test_configuration_commitment_mismatch_is_a_deterministic_conflict():
    engine, db, device = _db_and_device()
    ledger = FakeLedger(state={
        "state_numeric": 1,
        "state_label": "REGISTERED",
        "owner_hash": OWNER_HASH,
        "config_hash": "f" * 64,
        "stamp_valid": False,
        "last_verifier": "PRIVATE-ADDRESS",
        "updated_at": 1,
    })
    try:
        facts = PassportFactsService(db, ledger_reader=ledger, history_reader=FakeHistory()).build(device)
        assert facts["reconciliation"]["status"] == "CONFLICT"
        check = next(item for item in facts["reconciliation"]["checks"] if item["field"] == "configuration_commitment")
        assert check["result"] == "CONFLICT"
        assert "f" * 64 not in str(facts)
    finally:
        _cleanup(engine, db)


def test_indexer_lag_results_in_pending_sync_not_a_final_conflict():
    engine, db, device = _db_and_device()
    ledger = FakeLedger(last_round=500200)
    history = FakeHistory(indexer_round=500190)
    try:
        facts = PassportFactsService(db, ledger_reader=ledger, history_reader=history).build(device)
        assert facts["reconciliation"]["status"] == "PENDING_SYNC"
        assert any("behind" in note.lower() for note in facts["limitations"])
    finally:
        _cleanup(engine, db)


def test_protected_current_state_is_not_returned_or_named_in_summary():
    engine, db, device = _db_and_device(status=DeviceStatus.DISPUTED)
    ledger = FakeLedger(state={
        "state_numeric": 4,
        "state_label": "DISPUTED",
        "owner_hash": OWNER_HASH,
        "config_hash": config_hash(CONFIG),
        "stamp_valid": False,
        "last_verifier": "PRIVATE-ADDRESS",
        "updated_at": 1,
    })
    try:
        facts = PassportFactsService(db, ledger_reader=ledger, history_reader=FakeHistory()).build(device)
        assert facts["reconciliation"]["status"] == "PARTIAL"
        assert facts["reconciliation"]["checks"] == []
        assert "DISPUTED" not in str(facts)
        assert "PRIVATE-ADDRESS" not in str(facts)
        assert OWNER_HASH not in str(facts)
    finally:
        _cleanup(engine, db)


def test_legacy_device_uses_stored_chain_identifier_not_synthetic_public_id():
    public_id = "TL-DELL-CCCCCCCCCCCCCCCCCCCCCCCC"
    legacy_chain_id = "TL-DL-OLD-LEGACY-CHAIN-KEY"
    engine, db, device = _db_and_device(
        identity_version=IdentityVersion.V1_LEGACY,
        registration_status=RegistrationStatus.LEGACY_TEST,
        public_id=public_id,
        device_id=legacy_chain_id,
        legacy_chain_id=legacy_chain_id,
        registration_round=None,
    )
    ledger = FakeLedger()
    history = FakeHistory(events=[], indexer_round=500100)
    try:
        facts = PassportFactsService(db, ledger_reader=ledger, history_reader=history).build(device)
        assert ledger.legacy_calls == [legacy_chain_id]
        assert ledger.v2_calls == []
        assert history.calls[0][0] == legacy_chain_id
        assert public_id not in str(facts)
        assert legacy_chain_id not in str(facts)
        assert facts["read_only"] is True
    finally:
        _cleanup(engine, db)


def test_missing_comparable_field_is_partial_not_a_false_match():
    engine, db, device = _db_and_device()
    ledger = FakeLedger(state={
        "state_numeric": 0,
        "state_label": "UNKNOWN",
        "owner_hash": OWNER_HASH,
        "config_hash": config_hash(CONFIG),
        "stamp_valid": False,
        "last_verifier": "PRIVATE-ADDRESS",
        "updated_at": 1,
    })
    try:
        facts = PassportFactsService(db, ledger_reader=ledger, history_reader=FakeHistory()).build(device)
        assert facts["reconciliation"]["status"] == "PARTIAL"
        assert any(check["result"] == "NOT_COMPARABLE" for check in facts["reconciliation"]["checks"])
    finally:
        _cleanup(engine, db)
