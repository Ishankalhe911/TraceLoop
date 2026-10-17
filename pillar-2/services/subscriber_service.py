"""Mirror TraceLoop Algorand ARC-28 events into the private database.

Identity contract:
- The on-chain device key in event arguments is Device.public_id (public).
- LedgerEvent.device_id is a foreign key to Device.id (private internal UUID).
- Event metadata may contain public IDs and chain facts, but never raw serials.

Reliability contract:
- Events are idempotent by transaction ID + log index.
- The watermark advances only after the searched range has been completely
  paginated and every recognized event in that range has been mirrored.
- A recognized event that cannot be decoded or mapped to a local device blocks
  the watermark beyond its round, so the event is retried instead of lost.
"""

import base64
import logging
import os
import re
import struct
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from algosdk.encoding import checksum, encode_address
from algosdk.v2client import indexer
from sqlalchemy.exc import IntegrityError

from database import ChainStatus, Device, LedgerEvent, SessionLocal
from services.device_identity import is_public_id

INDEXER_URL = os.getenv("INDEXER_URL", "https://testnet-idx.algonode.cloud")
INDEXER_TOKEN = os.getenv("INDEXER_TOKEN", "")
APP_ID = int(os.getenv("APP_ID", "771258340"))
# Configure this to the deployment round of the selected APP_ID.
START_ROUND = int(os.getenv("SUBSCRIBER_START_ROUND", "50000000"))
WATERMARK_PATH = os.getenv("SUBSCRIBER_WATERMARK_PATH", "subscriber_watermark.txt")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("TraceLoopSubscriber")


def get_selector(signature: str) -> bytes:
    """Return the four-byte ARC-28 event selector (SHA-512/256 prefix)."""
    return checksum(signature.encode("utf-8"))[:4]


# --- PuyaPy ARC-4 event decoders ---
# Dynamic string arguments use two-byte offsets in the ARC-4 tuple head; static
# uint64/address arguments are inline. The decoder validates lengths and offsets
# before slicing untrusted Indexer response data.


def decode_arc4_string(data: bytes, offset: int) -> Tuple[str, int]:
    """Decode a two-byte-length-prefixed UTF-8 string at offset."""
    if offset < 0 or offset + 2 > len(data):
        raise ValueError("ARC-4 string length prefix is out of bounds")
    length = struct.unpack_from(">H", data, offset)[0]
    value_start = offset + 2
    value_end = value_start + length
    if value_end > len(data):
        raise ValueError("ARC-4 string value is truncated")
    return data[value_start:value_end].decode("utf-8"), value_end


def decode_arc4_uint64(data: bytes, offset: int) -> Tuple[int, int]:
    """Decode a big-endian uint64 at offset."""
    if offset < 0 or offset + 8 > len(data):
        raise ValueError("ARC-4 uint64 is out of bounds")
    return struct.unpack_from(">Q", data, offset)[0], offset + 8


def decode_arc4_address(data: bytes, offset: int) -> Tuple[str, int]:
    """Decode a 32-byte Algorand address at offset."""
    if offset < 0 or offset + 32 > len(data):
        raise ValueError("ARC-4 address is out of bounds")
    return encode_address(data[offset:offset + 32]), offset + 32


# Event name, event argument types, and mirrored event type. The signature
# strings must match the event declarations in the deployed smart contract.
ARC28_EVENTS = {
    get_selector("DeviceRegistered(string,string,uint64)"): {
        "name": "DeviceRegistered",
        "args": ["string", "string", "uint64"],
        "event_type": "REGISTERED",
    },
    get_selector("DeviceVerified(string,address,string,uint64)"): {
        "name": "DeviceVerified",
        "args": ["string", "address", "string", "uint64"],
        "event_type": "VERIFIED",
    },
    get_selector("DeviceTransferred(string,string,address,uint64)"): {
        "name": "DeviceTransferred",
        "args": ["string", "string", "address", "uint64"],
        "event_type": "TRANSFERRED",
    },
    get_selector("HardwareChangeDeclared(string,string,uint64)"): {
        "name": "HardwareChangeDeclared",
        "args": ["string", "string", "uint64"],
        "event_type": "HARDWARE_CHANGED",
    },
    get_selector("DisputeRaised(string,address,uint64,uint64)"): {
        "name": "DisputeRaised",
        "args": ["string", "address", "uint64", "uint64"],
        "event_type": "DISPUTED",
    },
    get_selector("DisputeResolved(string,uint64,uint64)"): {
        "name": "DisputeResolved",
        "args": ["string", "uint64", "uint64"],
        "event_type": "DISPUTE_RESOLVED",
    },
    get_selector("DeviceRecycled(string,address,uint64)"): {
        "name": "DeviceRecycled",
        "args": ["string", "address", "uint64"],
        "event_type": "RECYCLED",
    },
    get_selector("DeviceExported(string,uint64)"): {
        "name": "DeviceExported",
        "args": ["string", "uint64"],
        "event_type": "EXPORTED",
    },
}


def _arc4_head_size(arg_types: List[str]) -> int:
    size = 0
    for arg_type in arg_types:
        if arg_type == "string":
            size += 2
        elif arg_type == "uint64":
            size += 8
        elif arg_type == "address":
            size += 32
        else:
            raise ValueError(f"Unsupported ARC-28 event argument type: {arg_type}")
    return size


def decode_event_payload(arg_types: List[str], payload: bytes) -> List[Any]:
    """Decode the known ARC-28 event tuple payload after its four-byte selector."""
    head_size = _arc4_head_size(arg_types)
    if len(payload) < head_size:
        raise ValueError("ARC-4 event payload is shorter than its tuple head")

    values: List[Any] = []
    head_cursor = 0
    for arg_type in arg_types:
        if arg_type == "uint64":
            value, head_cursor = decode_arc4_uint64(payload, head_cursor)
            values.append(value)
        elif arg_type == "address":
            value, head_cursor = decode_arc4_address(payload, head_cursor)
            values.append(value)
        elif arg_type == "string":
            offset = struct.unpack_from(">H", payload, head_cursor)[0]
            head_cursor += 2
            # Dynamic strings must point after the complete tuple head and into
            # the payload. The decoder then verifies the length-prefixed tail.
            if offset < head_size or offset >= len(payload):
                raise ValueError("ARC-4 string offset is outside the event payload")
            value, _ = decode_arc4_string(payload, offset)
            values.append(value)
        else:  # Defensive if the event table is extended incorrectly.
            raise ValueError(f"Unsupported ARC-28 event argument type: {arg_type}")

    return values


class SubscriberDaemon:
    def __init__(self):
        self.indexer = indexer.IndexerClient(INDEXER_TOKEN, INDEXER_URL)
        self.app_id = APP_ID
        self.poll_interval = 5
        self.watermark_path = Path(os.getenv("SUBSCRIBER_WATERMARK_PATH", WATERMARK_PATH))
        self.last_round = self._load_watermark()

    def _load_watermark(self) -> int:
        """Load the last fully processed round; initial cursor includes START_ROUND."""
        if self.watermark_path.exists():
            try:
                raw = self.watermark_path.read_text(encoding="utf-8").strip()
                if raw:
                    saved = int(raw)
                    if saved >= START_ROUND:
                        return saved
                    logger.warning(
                        "Stale subscriber watermark (%s < start round %s); resetting.",
                        saved,
                        START_ROUND,
                    )
            except (OSError, ValueError) as exc:
                logger.warning(
                    "Could not read subscriber watermark (exception_type=%s); resetting.",
                    type(exc).__name__,
                )

        # search_transactions uses min_round=self.last_round + 1.
        return START_ROUND - 1

    def _save_watermark(self, round_num: int) -> None:
        """Atomically persist a monotonic watermark after successful processing."""
        if round_num <= self.last_round:
            return
        self.watermark_path.parent.mkdir(parents=True, exist_ok=True)
        temp_name: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(self.watermark_path.parent),
                prefix=f".{self.watermark_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_name = handle.name
                handle.write(str(round_num))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.watermark_path)
            self.last_round = round_num
        except Exception:
            if temp_name:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
            raise

    def process_logs(self, txn: dict) -> bool:
        """Mirror recognized events in one transaction; False means retry needed."""
        logs = txn.get("logs", []) or []
        txn_id = txn.get("id")
        if logs and not txn_id:
            logger.warning("Transaction with logs has no transaction ID; deferring watermark.")
            return False

        all_ok = True
        for log_index, b64_log in enumerate(logs):
            try:
                log_bytes = base64.b64decode(b64_log, validate=True)
                if len(log_bytes) < 4:
                    # Unknown/non-ARC-28 log: no recognized event to mirror.
                    continue
                event_def = ARC28_EVENTS.get(log_bytes[:4])
                if event_def is None:
                    continue

                payload = log_bytes[4:]
                decoded = decode_event_payload(event_def["args"], payload)
                if not self.mirror_to_database(txn, event_def, decoded, log_index=log_index):
                    all_ok = False
            except Exception as exc:
                # Never log payload bytes, decoded owner hashes, or private DB rows.
                logger.warning(
                    "Could not mirror recognized/malformed log (tx=%s, log_index=%s, exception_type=%s); deferring watermark.",
                    str(txn.get("id", "unknown"))[:12],
                    log_index,
                    type(exc).__name__,
                )
                all_ok = False
        return all_ok

    @staticmethod
    def _event_times(txn: dict, decoded: List[Any]) -> Tuple[datetime, Optional[datetime], Optional[int], str]:
        """Return event timestamp, confirmed_at, chain timestamp and source label.

        A local clock fallback is labelled explicitly and is never represented as
        a chain timestamp or confirmation timestamp.
        """
        round_time = txn.get("round-time")
        if isinstance(round_time, (int, float)) and round_time > 0:
            block_dt = datetime.fromtimestamp(int(round_time), tz=timezone.utc)
            return block_dt, block_dt, int(round_time), "block"

        event_ts = decoded[-1] if decoded and isinstance(decoded[-1], int) else None
        if event_ts is not None and event_ts > 0:
            event_dt = datetime.fromtimestamp(event_ts, tz=timezone.utc)
            return event_dt, None, event_ts, "event"

        return datetime.now(timezone.utc), None, None, "local_fallback"

    def mirror_to_database(
        self,
        txn: dict,
        event_def: dict,
        decoded: List[Any],
        log_index: int = 0,
    ) -> bool:
        """Map public on-chain ID to internal Device.id and idempotently store event."""
        public_id = decoded[0] if decoded else None
        txn_id = txn.get("id")
        confirmed_round = txn.get("confirmed-round")

        if not txn_id or not isinstance(confirmed_round, int):
            logger.warning("Event transaction is missing ID or confirmed round; deferring watermark.")
            return False
        if not is_public_id(public_id):
            # Phase 1 test registrations used TL-{brand_code}-{serial}. Those
            # known legacy IDs are test-only and cannot be mapped after Phase 2.
            # Skip just that legacy shape so old test events do not pin the watermark;
            # unknown malformed IDs still defer the watermark for investigation.
            legacy_test_id = (
                isinstance(public_id, str)
                and re.fullmatch(
                    r"TL-(?:DL|DELL|HP|LN|LENOVO|AS|ASUS|AC|ACER|AP|APPLE|MS|MSI|SG|SAMSUNG)-.+",
                    public_id,
                ) is not None
            )
            if legacy_test_id:
                logger.warning(
                    "Skipping legacy test device ID in historical event (tx=%s).",
                    str(txn_id)[:12],
                )
                return True
            logger.error(
                "Event has an invalid device ID (tx=%s); deferring watermark.",
                str(txn_id)[:12],
            )
            return False

        event_name = event_def["name"]
        event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"traceloop-ledger-event:{txn_id}:{log_index}"))
        event_time, confirmed_at, chain_timestamp, timestamp_source = self._event_times(txn, decoded)

        metadata: Dict[str, Any] = {
            "event_name": event_name,
            "public_id": public_id,
            "chain_timestamp": chain_timestamp,
            "timestamp_source": timestamp_source,
            "sender": txn.get("sender"),
            "log_index": log_index,
        }
        if event_name == "DeviceRegistered":
            metadata["owner_hash"] = decoded[1]
        elif event_name == "DeviceVerified":
            metadata["verifier"] = decoded[1]
            metadata["config_hash"] = decoded[2]
        elif event_name == "DeviceTransferred":
            metadata["new_owner_hash"] = decoded[1]
            metadata["verifier"] = decoded[2]
        elif event_name == "HardwareChangeDeclared":
            metadata["new_config_hash"] = decoded[1]
        elif event_name == "DisputeRaised":
            metadata["raised_by"] = decoded[1]
            metadata["dispute_type"] = decoded[2]
        elif event_name == "DisputeResolved":
            metadata["resolved_state"] = decoded[1]
        elif event_name == "DeviceRecycled":
            metadata["recycler"] = decoded[1]

        db = SessionLocal()
        try:
            # CRITICAL IDENTITY MAPPING: the event carries the public on-chain ID,
            # while LedgerEvent.device_id is a FK to the private internal UUID.
            device = db.query(Device).filter(Device.public_id == public_id).one_or_none()
            if device is None:
                logger.warning(
                    "No local device maps to public Passport ID %s (tx=%s); deferring watermark.",
                    public_id,
                    str(txn_id)[:12],
                )
                return False

            existing = db.query(LedgerEvent).filter_by(id=event_id).one_or_none()
            if existing is None:
                existing = LedgerEvent(
                    id=event_id,
                    device_id=device.id,
                    event_type=event_def["event_type"],
                    actor_type="SYSTEM",
                    tx_hash=txn_id,
                    block_number=confirmed_round,
                    chain_status=ChainStatus.CONFIRMED,
                    metadata_=metadata,
                    timestamp=event_time,
                    confirmed_at=confirmed_at,
                )
                db.add(existing)
            else:
                # Reprocessing a transaction is expected after a failed round.
                # Update this exact log's row without collapsing other logs from
                # the same transaction into it.
                existing.device_id = device.id
                existing.event_type = event_def["event_type"]
                existing.actor_type = "SYSTEM"
                existing.tx_hash = txn_id
                existing.block_number = confirmed_round
                existing.chain_status = ChainStatus.CONFIRMED
                existing.metadata_ = metadata
                existing.timestamp = event_time
                existing.confirmed_at = confirmed_at

            db.commit()
            logger.info(
                "Mirrored %s for Passport ID %s at round %s (log_index=%s).",
                event_name,
                public_id,
                confirmed_round,
                log_index,
            )
            return True

        except IntegrityError:
            db.rollback()
            # A concurrent subscriber may have inserted the deterministic event
            # ID. If so, treat this log as successfully mirrored; otherwise retry.
            try:
                exists = db.query(LedgerEvent).filter_by(id=event_id).one_or_none()
                if exists is not None:
                    return True
            except Exception:
                pass
            logger.warning(
                "Database integrity error mirroring %s (tx=%s); deferring watermark.",
                event_name,
                str(txn_id)[:12],
            )
            return False
        except Exception as exc:
            db.rollback()
            logger.error(
                "Database error mirroring %s (tx=%s, exception_type=%s); deferring watermark.",
                event_name,
                str(txn_id)[:12],
                type(exc).__name__,
            )
            return False
        finally:
            db.close()

    def _fetch_all_transactions(self, min_round: int) -> Tuple[List[dict], Optional[int]]:
        """Fetch every page for this app from min_round onward.

        Algorand Indexer returns ``next-token``. py-algorand-sdk exposes this as
        the ``next_page`` argument and serializes it to the REST ``next`` query.
        """
        transactions: List[dict] = []
        current_round: Optional[int] = None
        next_token: Optional[str] = None
        seen_tokens = set()

        while True:
            params: Dict[str, Any] = {
                "application_id": self.app_id,
                "min_round": min_round,
            }
            if next_token:
                if next_token in seen_tokens:
                    raise RuntimeError("Indexer repeated a pagination token")
                seen_tokens.add(next_token)
                params["next_page"] = next_token

            response = self.indexer.search_transactions(**params)
            transactions.extend(response.get("transactions", []) or [])
            response_round = response.get("current-round")
            if isinstance(response_round, int):
                current_round = response_round if current_round is None else max(current_round, response_round)

            returned_token = response.get("next-token") or response.get("next_token")
            if not returned_token:
                break
            next_token = returned_token

        return transactions, current_round

    def sync_once(self) -> bool:
        """Fetch and mirror a complete Indexer range; return False if it needs retry."""
        min_round = self.last_round + 1
        try:
            transactions, indexer_round = self._fetch_all_transactions(min_round)
            transactions.sort(
                key=lambda txn: (
                    int(txn.get("confirmed-round", min_round)),
                    int(txn.get("intra-round-offset", 0)),
                    str(txn.get("id", "")),
                )
            )

            failed_rounds: List[int] = []
            for txn in transactions:
                round_num = txn.get("confirmed-round")
                if not isinstance(round_num, int):
                    failed_rounds.append(min_round)
                    continue
                if not self.process_logs(txn):
                    failed_rounds.append(round_num)

            if failed_rounds:
                # Keep the failed round itself uncommitted. Transactions at the
                # same round will be fetched again, and deterministic event IDs
                # make already-mirrored logs safe to replay.
                safe_round = min(failed_rounds) - 1
                if safe_round > self.last_round:
                    self._save_watermark(safe_round)
                logger.error(
                    "Subscriber sync incomplete; will retry from round %s.",
                    self.last_round + 1,
                )
                return False

            completed_round = max(
                [self.last_round]
                + ([txn["confirmed-round"] for txn in transactions if isinstance(txn.get("confirmed-round"), int)])
                + ([indexer_round] if indexer_round is not None else [])
            )
            self._save_watermark(completed_round)
            logger.debug("Subscriber watermark now at round %s.", self.last_round)
            return True

        except Exception as exc:
            # Do not advance watermark on Indexer/network/pagination/file failure.
            logger.error("Indexer sync failed (exception_type=%s); watermark unchanged.", type(exc).__name__)
            return False

    def start(self) -> None:
        logger.info(
            "Starting TraceLoop Subscriber Daemon (APP_ID=%s, start_round=%s, watermark=%s).",
            self.app_id,
            START_ROUND,
            self.last_round,
        )
        while True:
            self.sync_once()
            time.sleep(self.poll_interval)


if __name__ == "__main__":
    SubscriberDaemon().start()
