"""Deterministic public-passport facts and reconciliation.

No language model participates in identity resolution, source retrieval, field
comparison, lifecycle ordering, or status selection. This service assembles a
small public-safe result from (1) fresh read-only Algod state, (2) independent
confirmed Indexer events, and (3) allowlisted PostgreSQL fields / event mirror.
Private IDs, serials, owner/config hashes, raw metadata, evidence, and dispute
records never leave this service.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from database import (
    ChainStatus,
    Device,
    DeviceStatus,
    IdentityVersion,
    LedgerEvent,
    RegistrationStatus,
    Transfer,
    TransferStatus,
    RecyclingRecord,
    RecyclingStatus,
)
from services.chain_history_service import ChainHistoryService, PUBLIC_EVENT_TYPES

logger = logging.getLogger(__name__)

PUBLIC_STATE_VALUES = {"REGISTERED", "VERIFIED", "TRANSFERRED", "RECYCLED", "EXPORTED"}
CHECK_STATUS_VALUES = {"MATCH", "CONFLICT", "NOT_COMPARABLE"}

RECONCILIATION_HEADLINES = {
    "MATCH": "The comparable current records match.",
    "CONFLICT": "At least one comparable database field differs from the latest chain state.",
    "PENDING_SYNC": "The chain history may not have caught up; treat comparisons as provisional.",
    "OFFCHAIN_ONLY": "A mirrored event has no matching event in the completely scanned chain window.",
    "CHAIN_ONLY": "A confirmed chain event is missing from the local event mirror.",
    "SOURCE_UNAVAILABLE": "A required source could not be checked, so reconciliation is incomplete.",
    "PARTIAL": "A public comparison is available, but not every current-state field can be compared.",
}


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _status_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    normalized = str(_enum_value(value)).upper()
    return normalized if normalized else None


def _utc_iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _config_hash_candidates(config: Any) -> Set[str]:
    """Known serialization variants already used by current TraceLoop routes.

    Registration uses compact separators while verification/hardware-update
    paths have historically used json.dumps(sort_keys=True) with spaces. Both
    are included explicitly to avoid treating serialization-only differences
    as real configuration conflicts. No other heuristic comparison is allowed.
    """
    if not isinstance(config, dict):
        return set()
    candidates: Set[str] = set()
    for separators in (None, (",", ":")):
        try:
            if separators is None:
                serialized = json.dumps(config, sort_keys=True)
            else:
                serialized = json.dumps(config, sort_keys=True, separators=separators)
            candidates.add(hashlib.sha256(serialized.encode("utf-8")).hexdigest())
        except (TypeError, ValueError):
            continue
    return candidates


def _public_mirror_key(event: LedgerEvent) -> Optional[Tuple[str, str, int, Optional[int]]]:
    tx_id = event.tx_hash
    event_type = _status_text(event.event_type)
    round_num = event.block_number
    if not tx_id or event_type not in PUBLIC_EVENT_TYPES or round_num is None:
        return None
    log_index: Optional[int] = None
    metadata = event.metadata_ if isinstance(event.metadata_, dict) else {}
    raw_index = metadata.get("log_index")
    if isinstance(raw_index, int) and raw_index >= 0:
        log_index = raw_index
    return str(tx_id), event_type, int(round_num), log_index


class PassportFactsService:
    """Builds deterministic public facts for a DB-resolved Device record."""

    def __init__(
        self,
        db: Session,
        *,
        ledger_reader=None,
        history_reader: Optional[ChainHistoryService] = None,
        clock=None,
    ) -> None:
        self.db = db
        if ledger_reader is None:
            from services.ledger_service import ledger_service
            ledger_reader = ledger_service
        self.ledger = ledger_reader
        self.history = history_reader or ChainHistoryService()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _is_legacy(device: Device) -> bool:
        return (
            _status_text(device.identity_version) == IdentityVersion.V1_LEGACY.value
            or _status_text(device.registration_status) == RegistrationStatus.LEGACY_TEST.value
        )

    def build(self, device: Device) -> Dict[str, Any]:
        """Return public-safe facts; the caller must already resolve Device by public ID."""
        is_legacy = self._is_legacy(device)
        registration_status = _status_text(device.registration_status)
        identity_version = _status_text(device.identity_version) or "UNKNOWN"
        chain_key = device.legacy_chain_id if is_legacy else device.public_id
        source_notes: List[str] = []

        chain_state: Optional[Dict[str, Any]] = None
        chain_state_available = False
        if not chain_key:
            source_notes.append("The stored historical chain key is unavailable.")
        else:
            try:
                if is_legacy:
                    chain_state = self.ledger.get_legacy_device_state(chain_key)
                else:
                    chain_state = self.ledger.get_device_state(device.public_id)
                chain_state_available = isinstance(chain_state, dict)
                if not chain_state_available:
                    source_notes.append("The current chain state returned an unexpected response.")
            except Exception as exc:
                # Exception text can contain provider details; never return/log it.
                logger.warning("Passport state read failed (exception_type=%s)", type(exc).__name__)
                source_notes.append("The current chain state could not be retrieved.")

        chain_head_round = self._get_algod_head_round()
        registration_round = device.registration_confirmed_round
        history: Dict[str, Any]
        if chain_key:
            try:
                history = self.history.get_device_history(
                    chain_key,
                    registration_round=int(registration_round) if registration_round else None,
                )
            except Exception as exc:
                logger.warning("Passport history read failed (exception_type=%s)", type(exc).__name__)
                history = {
                    "status": "UNAVAILABLE",
                    "complete": False,
                    "events": [],
                    "history_from_round": None,
                    "indexer_through_round": None,
                    "transactions_scanned": 0,
                    "pages_scanned": 0,
                    "failure_code": "HISTORY_SERVICE_FAILED",
                    "decode_warnings": 0,
                }
        else:
            history = {
                "status": "UNAVAILABLE",
                "complete": False,
                "events": [],
                "history_from_round": None,
                "indexer_through_round": None,
                "transactions_scanned": 0,
                "pages_scanned": 0,
                "failure_code": "LEGACY_CHAIN_KEY_UNAVAILABLE",
                "decode_warnings": 0,
            }

        try:
            mirror_rows = (
                self.db.query(LedgerEvent)
                .filter(
                    LedgerEvent.device_id == device.id,
                    LedgerEvent.chain_status == ChainStatus.CONFIRMED,
                    LedgerEvent.event_type.in_(tuple(PUBLIC_EVENT_TYPES)),
                )
                .order_by(LedgerEvent.block_number.asc(), LedgerEvent.timestamp.asc())
                .all()
            )
            mirror_available = True
        except Exception as exc:
            logger.warning("Passport mirror read failed (exception_type=%s)", type(exc).__name__)
            mirror_rows = []
            mirror_available = False
            source_notes.append("The local event mirror could not be queried.")

        # Do not expose state labels or mismatch reasons if either current source
        # reports the protected DISPUTED state. The rest of the public response
        # stays generic and omits that protected field entirely.
        db_state = _status_text(device.status)
        raw_chain_state = _status_text(chain_state.get("state_label")) if chain_state else None
        protected_state_present = (
            db_state == DeviceStatus.DISPUTED.value
            or raw_chain_state == DeviceStatus.DISPUTED.value
        )

        safe_db_state = db_state if db_state in PUBLIC_STATE_VALUES and not protected_state_present else None
        safe_chain_state = (
            raw_chain_state
            if raw_chain_state in PUBLIC_STATE_VALUES and not protected_state_present
            else None
        )

        checks: List[Dict[str, str]] = []
        if chain_state_available and chain_state and not protected_state_present:
            self._add_check(
                checks,
                "lifecycle_state",
                safe_db_state,
                safe_chain_state,
                "The current lifecycle labels are compared without exposing owner details.",
            )

            db_owner_hash = getattr(device, "current_owner_hash", None)
            chain_owner_hash = chain_state.get("owner_hash")
            self._add_check(
                checks,
                "ownership_commitment",
                db_owner_hash,
                chain_owner_hash,
                "The database and chain ownership commitments are compared privately; raw values are never returned.",
            )

            chain_config_hash = chain_state.get("config_hash")
            local_config_hashes = _config_hash_candidates(getattr(device, "current_config", None))
            if isinstance(chain_config_hash, str) and local_config_hashes:
                checks.append({
                    "field": "configuration_commitment",
                    "result": "MATCH" if chain_config_hash in local_config_hashes else "CONFLICT",
                    "explanation": (
                        "The current configuration commitment matches the database configuration."
                        if chain_config_hash in local_config_hashes else
                        "The current configuration commitment differs from the database configuration."
                    ),
                })
            else:
                checks.append({
                    "field": "configuration_commitment",
                    "result": "NOT_COMPARABLE",
                    "explanation": "A comparable configuration commitment was not available from both sources.",
                })

            local_stamp = getattr(device, "stamp_valid", None)
            chain_stamp = chain_state.get("stamp_valid")
            if isinstance(local_stamp, bool) and isinstance(chain_stamp, bool):
                checks.append({
                    "field": "verification_stamp",
                    "result": "MATCH" if local_stamp is chain_stamp else "CONFLICT",
                    "explanation": (
                        "The stamp-valid flag matches between the database and chain."
                        if local_stamp is chain_stamp else
                        "The stamp-valid flag differs between the database and chain."
                    ),
                })
            else:
                checks.append({
                    "field": "verification_stamp",
                    "result": "NOT_COMPARABLE",
                    "explanation": "A comparable stamp-valid flag was not available from both sources.",
                })
        else:
            # Avoid returning individual check results if the current state is
            # protected or unavailable; callers cannot infer a private mismatch.
            checks = []

        chain_events = list(history.get("events") or [])
        mirror_keys = [key for key in (_public_mirror_key(row) for row in mirror_rows) if key]
        chain_keys = [
            (
                str(event.get("transaction_ref")),
                str(event.get("event_type", "")).upper(),
                int(event.get("round", -1)),
                int(event.get("log_index", -1)),
            )
            for event in chain_events
            if event.get("transaction_ref") and event.get("round") is not None
        ]
        chain_only_events: List[Dict[str, Any]] = []
        offchain_only_events: List[Dict[str, Any]] = []

        chain_key_set = set(chain_keys)
        mirror_key_set = set(mirror_keys)
        for event, key in zip(
            [e for e in chain_events if e.get("transaction_ref") and e.get("round") is not None],
            chain_keys,
        ):
            exact = key in mirror_key_set
            legacy_match = any(m[:3] == key[:3] and m[3] is None for m in mirror_key_set)
            if not exact and not legacy_match:
                chain_only_events.append({
                    "event_type": event.get("event_type"),
                    "round": event.get("round"),
                    "transaction_ref": event.get("transaction_ref"),
                })

        history_from = history.get("history_from_round")
        indexer_through = history.get("indexer_through_round")
        history_complete = bool(history.get("status") == "AVAILABLE" and history.get("complete"))
        if history_complete and indexer_through is not None:
            for key in mirror_key_set:
                tx_ref, event_type, round_num, log_idx = key
                if history_from is not None and round_num < int(history_from):
                    continue
                if round_num > int(indexer_through):
                    continue
                exact = key in chain_key_set
                legacy_match = log_idx is None and any(c[:3] == key[:3] for c in chain_key_set)
                if not exact and not legacy_match:
                    offchain_only_events.append({
                        "event_type": event_type,
                        "round": round_num,
                        "transaction_ref": tx_ref,
                    })

        indexer_status = _status_text(history.get("status")) or "UNAVAILABLE"
        indexer_round = history.get("indexer_through_round")
        indexer_behind = (
            chain_head_round is not None
            and indexer_round is not None
            and int(indexer_round) < int(chain_head_round)
        )
        has_value_conflict = any(item["result"] == "CONFLICT" for item in checks)
        has_not_comparable = any(item["result"] == "NOT_COMPARABLE" for item in checks)
        has_partial_source = (
            not chain_state_available
            or indexer_status != "AVAILABLE"
            or not history_complete
            or not mirror_available
            or indexer_round is None
            or chain_head_round is None
        )

        if protected_state_present:
            reconciliation_status = "PARTIAL"
            source_notes.append("Some current-state fields are not included in this public response.")
        elif has_partial_source:
            reconciliation_status = "SOURCE_UNAVAILABLE" if (
                not chain_state_available
                or indexer_status == "UNAVAILABLE"
                or indexer_round is None
                or chain_head_round is None
                or not mirror_available
            ) else "PENDING_SYNC"
        elif indexer_behind:
            reconciliation_status = "PENDING_SYNC"
        elif has_value_conflict:
            reconciliation_status = "CONFLICT"
        elif offchain_only_events:
            reconciliation_status = "OFFCHAIN_ONLY"
        elif chain_only_events:
            reconciliation_status = "CHAIN_ONLY"
        elif has_not_comparable:
            reconciliation_status = "PARTIAL"
        else:
            reconciliation_status = "MATCH"

        if is_legacy:
            source_notes.append(
                "This is a read-only historical V1 passport; the synthetic public lookup ID is not used as a chain key."
            )
        if not registration_round:
            source_notes.append(
                "History search begins at the configured history start round because no registration round is stored."
            )
        if history.get("failure_code") == "HISTORY_PAGE_LIMIT_REACHED":
            source_notes.append("The history scan reached its safety page limit; older results may be incomplete.")
        elif history.get("decode_warnings"):
            source_notes.append("Some indexed logs could not be decoded, so history completeness is not guaranteed.")
        if chain_head_round is None:
            source_notes.append("The latest Algod round could not be checked, so Indexer lag could not be ruled out.")
        if indexer_behind:
            source_notes.append("The Indexer is behind the latest Algod round; history may still be catching up.")
        if chain_only_events:
            source_notes.append("At least one indexed chain event is not yet present in the local event mirror.")
        if offchain_only_events:
            source_notes.append("At least one confirmed mirrored event was not found in the scanned Indexer window.")

        summary = self._deterministic_summary(
            events=chain_events,
            db_state=safe_db_state,
            chain_state=safe_chain_state,
            reconciliation_status=reconciliation_status,
            is_legacy=is_legacy,
            protected_state_present=protected_state_present,
        )

        public_timeline = [
            {
                "event_type": event.get("event_type"),
                "description": event.get("description"),
                "timestamp": event.get("timestamp"),
                "round": event.get("round"),
                # The transaction reference is public chain provenance, not a
                # private database/device identity.
                "transaction_ref": event.get("transaction_ref"),
            }
            for event in chain_events
        ]

        # Public, device-scoped circularity facts. These are counts/statuses only;
        # do not infer avoided emissions or e-waste mass from a transfer/recycle event.
        try:
            completed_transfer_count = int(
                self.db.query(Transfer)
                .filter(Transfer.device_id == device.id, Transfer.status == TransferStatus.COMPLETED)
                .count()
            )
            recycling_rows = (
                self.db.query(RecyclingRecord)
                .filter(RecyclingRecord.device_id == device.id)
                .order_by(RecyclingRecord.completion_date.desc())
                .all()
            )
            confirmed_recycling = any(
                _status_text(row.recycling_status) == RecyclingStatus.RECYCLED.value
                and row.completion_date is not None
                for row in recycling_rows
            )
            circularity_impact = {
                "completed_transfer_count": completed_transfer_count,
                "confirmed_recycling_completion": confirmed_recycling,
                "measured_e_waste_mass_kg": None,
                "estimated_co2e_avoided_kg": None,
                "methodology_status": "COUNTS_ONLY_NO_UNVALIDATED_IMPACT_ESTIMATES",
                "note": "Transfers and recycling status are reported as recorded events. No avoided-emissions or mass estimate is made without measured mass and a validated life-cycle method.",
            }
        except Exception as exc:
            logger.warning("Passport circularity facts unavailable (exception_type=%s)", type(exc).__name__)
            circularity_impact = {
                "completed_transfer_count": None,
                "confirmed_recycling_completion": None,
                "measured_e_waste_mass_kg": None,
                "estimated_co2e_avoided_kg": None,
                "methodology_status": "SOURCE_UNAVAILABLE",
                "note": "Circularity facts could not be queried; unavailable values are not treated as zero.",
            }

        return {
            "summary": summary,
            "climate_circularity_impact": circularity_impact,
            "read_only": bool(is_legacy or registration_status != RegistrationStatus.CONFIRMED.value),
            "reconciliation": {
                "status": reconciliation_status,
                "headline": RECONCILIATION_HEADLINES[reconciliation_status],
                "checks": checks,
            },
            "timeline": public_timeline,
            "sources": {
                "current_chain_state": "AVAILABLE" if chain_state_available else "UNAVAILABLE",
                "independent_chain_history": (
                    "COMPLETE" if history_complete else
                    "PARTIAL" if indexer_status == "AVAILABLE" else "UNAVAILABLE"
                ),
                "database_record": "AVAILABLE",
                "database_event_mirror": "AVAILABLE" if mirror_available else "UNAVAILABLE",
                "history_from_round": history_from,
                "indexer_through_round": indexer_round,
                "algod_head_round": chain_head_round,
                "transactions_scanned": int(history.get("transactions_scanned") or 0),
                "pages_scanned": int(history.get("pages_scanned") or 0),
            },
            "limitations": _dedupe(source_notes),
            "checked_at": _utc_iso(self.clock()),
        }

    def _get_algod_head_round(self) -> Optional[int]:
        client = getattr(self.ledger, "client", None)
        status_method = getattr(client, "status", None)
        if not callable(status_method):
            return None
        try:
            result = status_method()
            value = result.get("last-round") if isinstance(result, dict) else None
            return int(value) if value is not None else None
        except Exception as exc:
            logger.info("Could not read Algod head round (exception_type=%s)", type(exc).__name__)
            return None

    @staticmethod
    def _add_check(
        checks: List[Dict[str, str]],
        field: str,
        db_value: Any,
        chain_value: Any,
        explanation: str,
    ) -> None:
        if db_value is None or chain_value is None:
            checks.append({
                "field": field,
                "result": "NOT_COMPARABLE",
                "explanation": f"A comparable value was not available for {field.replace('_', ' ')}.",
            })
            return
        matches = str(_enum_value(db_value)).upper() == str(_enum_value(chain_value)).upper()
        checks.append({
            "field": field,
            "result": "MATCH" if matches else "CONFLICT",
            "explanation": explanation if matches else (
                f"The database and chain {field.replace('_', ' ')} do not match."
            ),
        })

    @staticmethod
    def _deterministic_summary(
        *,
        events: List[Dict[str, Any]],
        db_state: Optional[str],
        chain_state: Optional[str],
        reconciliation_status: str,
        is_legacy: bool,
        protected_state_present: bool,
    ) -> str:
        if events:
            summary = (
                f"The indexed ledger history contains {len(events)} confirmed public lifecycle "
                f"event{'s' if len(events) != 1 else ''}. Latest visible event: "
                f"{events[-1].get('description', 'A lifecycle event was recorded on the ledger')}"
            )
        else:
            summary = (
                "No matching public lifecycle events were returned for the scanned history window. "
                "This is not proof that no earlier event exists outside that window."
            )

        if protected_state_present:
            summary += " Current-state details are limited in this public explanation."
        elif db_state and chain_state:
            summary += f" The database currently records {db_state}; the latest chain state is {chain_state}."

        if reconciliation_status == "MATCH":
            summary += " The comparable fields and indexed event mirror agree."
        elif reconciliation_status == "CONFLICT":
            summary += " At least one comparable current-state field differs; review the checks before relying on the record."
        elif reconciliation_status == "PENDING_SYNC":
            summary += " The history index or mirror may still be catching up, so any differences are provisional."
        elif reconciliation_status == "CHAIN_ONLY":
            summary += " A confirmed chain event is not yet reflected in the database event mirror."
        elif reconciliation_status == "OFFCHAIN_ONLY":
            summary += " A mirrored event was not found in the complete scanned chain window."
        else:
            summary += " The available sources were not sufficient for a complete reconciliation."

        if is_legacy:
            summary += " This is a read-only historical passport."
        return summary


def _dedupe(values: List[str]) -> List[str]:
    seen = set()
    output = []
    for value in values:
        if value and value not in seen:
            output.append(value)
            seen.add(value)
    return output
