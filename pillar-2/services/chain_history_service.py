"""Read-only, independent Algorand Indexer history for a single TraceLoop device.

This deliberately does not consult LedgerEvent while retrieving chain history.
The database mirror is compared later by PassportFactsService. That separation
lets reconciliation notice missing or stale mirror rows instead of treating
the mirror as independent chain evidence.
"""
from __future__ import annotations

import copy
import hashlib
import logging
import os
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from algosdk.v2client import indexer

from services.arc28_event_decoder import decode_arc28_log

logger = logging.getLogger(__name__)

# Public lookups can be repeated by many visitors. A small TTL cache avoids
# rescanning the same app history on every click. Cache keys hash the chain key
# so a V1 legacy key (which may contain old serial text) is not retained here.
_HISTORY_CACHE = OrderedDict()
_HISTORY_CACHE_LOCK = threading.Lock()
_HISTORY_CACHE_MAX_ITEMS = 256

PUBLIC_EVENT_TYPES = {
    "REGISTERED",
    "VERIFIED",
    "TRANSFERRED",
    "HARDWARE_CHANGED",
    "RECYCLED",
    "EXPORTED",
}

EVENT_DESCRIPTIONS = {
    "REGISTERED": "The device was registered on the ledger.",
    "VERIFIED": "A verification event was recorded on the ledger.",
    "TRANSFERRED": "A device transfer event was recorded on the ledger.",
    "HARDWARE_CHANGED": (
        "A hardware change was declared on the ledger; this does not independently "
        "prove that a physical change occurred."
    ),
    "RECYCLED": (
        "A recycler marked the device as recycled on the ledger; this alone does not "
        "independently verify the physical recycling process."
    ),
    "EXPORTED": "The device was marked as exported on the ledger.",
}


def _as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        converted = int(value)
    except (TypeError, ValueError):
        return None
    return converted if converted >= 0 else None


def _timestamp_for_transaction(txn: dict) -> Optional[str]:
    raw = txn.get("round-time")
    if isinstance(raw, (int, float)) and raw > 0:
        return datetime.fromtimestamp(int(raw), tz=timezone.utc).isoformat()
    return None


class ChainHistoryService:
    """Paginated confirmed-history reader with bounded work and explicit coverage."""

    def __init__(
        self,
        indexer_client=None,
        *,
        app_id: Optional[int] = None,
        start_round: Optional[int] = None,
        page_size: Optional[int] = None,
        max_pages: Optional[int] = None,
    ) -> None:
        self.app_id = int(app_id or os.getenv("APP_ID", "771258340"))
        self.indexer = indexer_client or indexer.IndexerClient(
            os.getenv("INDEXER_TOKEN", ""),
            os.getenv("INDEXER_URL", "https://testnet-idx.algonode.cloud"),
        )
        # The deployment's application creation round should be configured for
        # legacy records with no stored registration round. V2 normally starts
        # at its persisted registration_confirmed_round instead.
        configured_start = start_round
        if configured_start is None:
            configured_start = int(
                os.getenv(
                    "TRACELOOP_HISTORY_START_ROUND",
                    os.getenv("SUBSCRIBER_START_ROUND", "50000000"),
                )
            )
        self.start_round = max(1, int(configured_start))
        self.page_size = max(1, min(int(page_size or os.getenv("PASSPORT_HISTORY_PAGE_SIZE", "1000")), 1000))
        self.max_pages = max(1, int(max_pages or os.getenv("PASSPORT_HISTORY_MAX_PAGES", "20")))
        self.cache_ttl_seconds = max(0.0, float(os.getenv("PASSPORT_HISTORY_CACHE_TTL_SECONDS", "15")))

    def get_device_history(
        self,
        chain_key: str,
        *,
        registration_round: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Return public lifecycle events for an internally resolved chain key.

        ``chain_key`` must come from a Device row inside the backend; public
        callers must never supply a legacy chain key directly.
        """
        min_round = max(1, int(registration_round)) if registration_round else self.start_round
        result: Dict[str, Any] = {
            "status": "UNAVAILABLE",
            "complete": False,
            "events": [],
            "history_from_round": min_round,
            "indexer_through_round": None,
            "transactions_scanned": 0,
            "pages_scanned": 0,
            "failure_code": None,
            "decode_warnings": 0,
        }
        if not isinstance(chain_key, str) or not chain_key or len(chain_key.encode("utf-8")) > 120:
            result["failure_code"] = "INVALID_INTERNAL_CHAIN_KEY"
            return result

        cache_material = (
            f"{self.app_id}:{hashlib.sha256(chain_key.encode('utf-8')).hexdigest()}:"
            f"{min_round}:{self.page_size}:{self.max_pages}"
        )
        cache_key = hashlib.sha256(cache_material.encode("utf-8")).hexdigest()
        if self.cache_ttl_seconds > 0:
            now = time.monotonic()
            with _HISTORY_CACHE_LOCK:
                cached = _HISTORY_CACHE.get(cache_key)
                if cached and now - cached[0] <= self.cache_ttl_seconds:
                    _HISTORY_CACHE.move_to_end(cache_key)
                    return copy.deepcopy(cached[1])
                if cached:
                    _HISTORY_CACHE.pop(cache_key, None)

        events: List[Dict[str, Any]] = []
        current_round: Optional[int] = None
        next_token: Optional[str] = None
        seen_tokens: Set[str] = set()
        seen_event_keys: Set[tuple] = set()
        complete = True
        request_succeeded = False

        while result["pages_scanned"] < self.max_pages:
            params: Dict[str, Any] = {
                "application_id": self.app_id,
                "min_round": min_round,
                "limit": self.page_size,
            }
            if next_token:
                if next_token in seen_tokens:
                    complete = False
                    result["failure_code"] = "INDEXER_REPEATED_PAGE_TOKEN"
                    break
                seen_tokens.add(next_token)
                params["next_page"] = next_token

            try:
                response = self.indexer.search_transactions(**params)
                request_succeeded = True
            except Exception as exc:
                complete = False
                result["failure_code"] = "INDEXER_REQUEST_FAILED"
                logger.warning(
                    "Passport history Indexer request failed (exception_type=%s, pages=%s)",
                    type(exc).__name__,
                    result["pages_scanned"],
                )
                break

            result["pages_scanned"] += 1
            transactions = response.get("transactions", []) or []
            result["transactions_scanned"] += len(transactions)
            response_round = _as_int(response.get("current-round"))
            if response_round is not None:
                current_round = response_round if current_round is None else max(current_round, response_round)

            for txn in transactions:
                confirmed_round = _as_int(txn.get("confirmed-round"))
                tx_id = txn.get("id")
                logs = txn.get("logs", []) or []
                if not isinstance(logs, list):
                    complete = False
                    result["decode_warnings"] += 1
                    continue

                for log_index, encoded_log in enumerate(logs):
                    try:
                        decoded = decode_arc28_log(encoded_log)
                    except Exception as exc:
                        # We cannot determine which device a malformed recognized
                        # log belongs to. Do not present the history as exhaustive.
                        complete = False
                        result["decode_warnings"] += 1
                        logger.warning(
                            "Passport history event decoding failed (exception_type=%s, log_index=%s)",
                            type(exc).__name__,
                            log_index,
                        )
                        continue
                    if decoded is None:
                        continue
                    args = decoded["args"]
                    if not args or args[0] != chain_key:
                        continue
                    event_type = decoded["event_type"]
                    if event_type not in PUBLIC_EVENT_TYPES:
                        # Dispute events and their fields are intentionally not
                        # surfaced by public passport intelligence.
                        continue
                    if not tx_id or confirmed_round is None:
                        complete = False
                        result["decode_warnings"] += 1
                        continue

                    event_key = (str(tx_id), int(log_index))
                    if event_key in seen_event_keys:
                        continue
                    seen_event_keys.add(event_key)
                    events.append({
                        "event_type": event_type,
                        "description": EVENT_DESCRIPTIONS[event_type],
                        "transaction_ref": str(tx_id),
                        "round": confirmed_round,
                        "intra_round_offset": _as_int(txn.get("intra-round-offset")) or 0,
                        "log_index": log_index,
                        "timestamp": _timestamp_for_transaction(txn),
                    })

            returned_token = response.get("next-token") or response.get("next_token")
            if not returned_token:
                next_token = None
                break
            next_token = str(returned_token)
        else:
            # Hitting the configured safety cap while another page exists means
            # the returned history is partial, never an exhaustive empty history.
            complete = False
            result["failure_code"] = "HISTORY_PAGE_LIMIT_REACHED"

        events.sort(key=lambda item: (
            item["round"], item["intra_round_offset"], item["log_index"], item["transaction_ref"]
        ))
        result.update({
            "status": "AVAILABLE" if request_succeeded else "UNAVAILABLE",
            "complete": bool(complete and request_succeeded and next_token is None),
            "events": events,
            "indexer_through_round": current_round,
        })
        if request_succeeded and not result["complete"] and result["failure_code"] is None:
            result["failure_code"] = "HISTORY_INCOMPLETE"

        if (
            self.cache_ttl_seconds > 0
            and result["status"] == "AVAILABLE"
            and result["failure_code"] != "INDEXER_REQUEST_FAILED"
        ):
            with _HISTORY_CACHE_LOCK:
                _HISTORY_CACHE[cache_key] = (time.monotonic(), copy.deepcopy(result))
                _HISTORY_CACHE.move_to_end(cache_key)
                while len(_HISTORY_CACHE) > _HISTORY_CACHE_MAX_ITEMS:
                    _HISTORY_CACHE.popitem(last=False)
        return result
