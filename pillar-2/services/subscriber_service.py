import os
import time
import base64
import logging
import struct
import uuid
from datetime import datetime
from algosdk.v2client import indexer
from algosdk.abi import Method
from algosdk.encoding import encode_address

# Wired directly to the compliant PostgreSQL database.py
from database import SessionLocal
from database import LedgerEvent, ChainStatus

INDEXER_URL = os.getenv("INDEXER_URL", "https://testnet-idx.algonode.cloud")
INDEXER_TOKEN = os.getenv("INDEXER_TOKEN", "")
APP_ID = int(os.getenv("APP_ID", "770956001"))
# Set this to the round your contract was deployed.
# Find it on https://testnet.algoexplorer.io/application/{APP_ID}
START_ROUND = int(os.getenv("SUBSCRIBER_START_ROUND", "50000000"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("TraceLoopSubscriber")


def get_selector(signature: str) -> bytes:
    return Method.from_signature(signature).get_selector()


# --- PuyaPy ARC-4 manual decoders ---
# algosdk's ABIType.decode() does not handle PuyaPy's ARC-4 encoding correctly
# for dynamic types (strings). We decode manually from raw bytes.

def decode_arc4_string(data: bytes, offset: int) -> tuple[str, int]:
    """Read a 2-byte length-prefixed ARC-4 string at offset. Returns (value, new_offset)."""
    length = struct.unpack_from(">H", data, offset)[0]
    offset += 2
    value = data[offset: offset + length].decode("utf-8")
    return value, offset + length


def decode_arc4_uint64(data: bytes, offset: int) -> tuple[int, int]:
    """Read an 8-byte big-endian uint64 at offset. Returns (value, new_offset)."""
    value = struct.unpack_from(">Q", data, offset)[0]
    return value, offset + 8


def decode_arc4_address(data: bytes, offset: int) -> tuple[str, int]:
    """Read a 32-byte Algorand address at offset. Returns (checksum_address, new_offset)."""
    addr_bytes = data[offset: offset + 32]
    return encode_address(addr_bytes), offset + 32


# Event definitions: name, event_type, and ordered arg types for manual decode
# Types: "string", "address", "uint64"
ARC28_EVENTS = {
    get_selector("DeviceRegistered(string,string,uint64)"): {
        "name": "DeviceRegistered",
        "args": ["string", "string", "uint64"],
        "event_type": "REGISTERED"
    },
    get_selector("DeviceVerified(string,address,string,uint64)"): {
        "name": "DeviceVerified",
        "args": ["string", "address", "string", "uint64"],
        "event_type": "VERIFIED"
    },
    get_selector("DeviceTransferred(string,string,address,uint64)"): {
        "name": "DeviceTransferred",
        "args": ["string", "string", "address", "uint64"],
        "event_type": "TRANSFERRED"
    },
    get_selector("HardwareChangeDeclared(string,string,uint64)"): {
        "name": "HardwareChangeDeclared",
        "args": ["string", "string", "uint64"],
        "event_type": "VERIFIED"
    },
    get_selector("DisputeRaised(string,address,uint64,uint64)"): {
        "name": "DisputeRaised",
        "args": ["string", "address", "uint64", "uint64"],
        "event_type": "DISPUTED"
    },
    get_selector("DisputeResolved(string,uint64,uint64)"): {
        "name": "DisputeResolved",
        "args": ["string", "uint64", "uint64"],
        "event_type": "DISPUTE_RESOLVED"
    },
    get_selector("DeviceRecycled(string,address,uint64)"): {
        "name": "DeviceRecycled",
        "args": ["string", "address", "uint64"],
        "event_type": "RECYCLED"
    },
    get_selector("DeviceExported(string,uint64)"): {
        "name": "DeviceExported",
        "args": ["string", "uint64"],
        "event_type": "EXPORTED"
    }
}


def decode_event_payload(arg_types: list, payload: bytes) -> list:
    """
    Manually decode a PuyaPy ARC-4 event payload.
    PuyaPy emits a head section of 2-byte offsets for dynamic types,
    followed by the data section. We skip the head and decode sequentially.

    For simplicity and correctness with PuyaPy output, we decode the
    dynamic/static sections directly:
    - Static types (uint64, address) are fixed-width, decoded in place.
    - Dynamic types (string) have a 2-byte head pointer, then data in tail.
    """
    # Split into static head pointers and tail data
    # Head: 2 bytes per dynamic arg, 0 bytes per static arg
    # We do a two-pass: first compute head size, then decode

    head_size = 0
    for t in arg_types:
        if t == "string":
            head_size += 2  # offset pointer
        elif t == "uint64":
            head_size += 8
        elif t == "address":
            head_size += 32

    # Decode sequentially using a cursor
    # For dynamic types, the head holds a uint16 offset into the full payload
    # pointing to the actual data. For static types, data is inline in head.
    values = []
    head_cursor = 0

    for t in arg_types:
        if t == "uint64":
            val, head_cursor = decode_arc4_uint64(payload, head_cursor)
            values.append(val)
        elif t == "address":
            val, head_cursor = decode_arc4_address(payload, head_cursor)
            values.append(val)
        elif t == "string":
            # Head holds uint16 absolute offset to string data in payload
            offset = struct.unpack_from(">H", payload, head_cursor)[0]
            head_cursor += 2
            str_val, _ = decode_arc4_string(payload, offset)
            values.append(str_val)

    return values


class SubscriberDaemon:
    def __init__(self):
        self.indexer = indexer.IndexerClient(INDEXER_TOKEN, INDEXER_URL)
        self.app_id = APP_ID
        self.poll_interval = 5
        self.last_round = self._load_watermark()

    def _load_watermark(self) -> int:
      if os.path.exists("subscriber_watermark.txt"):
        with open("subscriber_watermark.txt", "r") as f:
            val = f.read().strip()
            if val:
                saved = int(val)
                # If saved watermark is before deployment, ignore it
                if saved >= START_ROUND:
                    return saved
                logger.info(f"Stale watermark {saved} < START_ROUND {START_ROUND}. Resetting.")
      logger.info(f"No watermark found. Starting from round {START_ROUND}.")
      return START_ROUND

    def _save_watermark(self, round_num: int):
        with open("subscriber_watermark.txt", "w") as f:
            f.write(str(round_num))
        self.last_round = round_num

    def process_logs(self, txn: dict):
        logs = txn.get("logs", [])
        for b64_log in logs:
            try:
                log_bytes = base64.b64decode(b64_log)
                if len(log_bytes) < 4:
                    continue

                selector = log_bytes[:4]
                if selector not in ARC28_EVENTS:
                    continue

                event_def = ARC28_EVENTS[selector]
                payload = log_bytes[4:]

                if not payload:
                    logger.warning(f"Empty payload for {event_def['name']} in txn {txn.get('id')}")
                    continue

                decoded = decode_event_payload(event_def["args"], payload)
                self.mirror_to_database(txn, event_def, decoded)

            except Exception as e:
                # Never let a bad log crash the daemon
                logger.warning(f"Skipping undecodable log in txn {txn.get('id')}: {e}")
                continue

    def mirror_to_database(self, txn: dict, event_def: dict, decoded: list):
        device_id = decoded[0]
        event_name = event_def["name"]

        metadata = {
            "event_name": event_name,
            "chain_timestamp": decoded[-1],
            "sender": txn.get("sender")
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

        logger.info(f"Mirrored {event_name} for device {device_id} to DB.")

        db = SessionLocal()
        try:
            # Idempotency check: Don't insert the same tx_hash twice
            exists = db.query(LedgerEvent).filter_by(tx_hash=txn["id"]).first()
            if not exists:
                new_event = LedgerEvent(
                    id=str(uuid.uuid4()),
                    device_id=device_id,
                    event_type=event_def["event_type"],
                    actor_type="SYSTEM",
                    tx_hash=txn["id"],
                    block_number=txn["confirmed-round"],
                    chain_status=ChainStatus.CONFIRMED,
                    metadata_=metadata,
                    confirmed_at=datetime.utcnow()
                )
                db.add(new_event)
                db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"DB Error: {e}")
        finally:
            db.close()

    def start(self):
        logger.info(f"Starting Trace-Loop Subscriber Daemon for App ID: {self.app_id}")
        while True:
            try:
                response = self.indexer.search_transactions(
                    application_id=self.app_id,
                    min_round=self.last_round + 1
                )

                transactions = response.get("transactions", [])
                if transactions:
                    transactions.sort(key=lambda x: x["confirmed-round"])
                    for txn in transactions:
                        self.process_logs(txn)
                        self._save_watermark(txn["confirmed-round"])

            except Exception as e:
                logger.error(f"Indexer polling failed: {e}")

            time.sleep(self.poll_interval)


if __name__ == "__main__":
    daemon = SubscriberDaemon()
    daemon.start()