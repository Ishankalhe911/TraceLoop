import os
import time
import base64
import logging
import uuid
from datetime import datetime
from algosdk.v2client import indexer
from algosdk.abi import Method, ABIType

# Assume database setup exists in their Pillar 2 codebase
# from database import SessionLocal, LedgerEvent

# --- CONFIGURATION ---
INDEXER_URL = os.getenv("INDEXER_URL", "https://testnet-idx.algonode.cloud")
INDEXER_TOKEN = os.getenv("INDEXER_TOKEN", "")
APP_ID = int(os.getenv("APP_ID", "770956001")) # Final TestNet App ID[cite: 3]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("TraceLoopSubscriber")

# --- ARC-28 EVENT DECODERS ---
def get_selector(signature: str) -> bytes:
    """Calculates the 4-byte selector for an ARC-28 event signature."""
    return Method.from_signature(signature).get_selector()

# These signatures map exactly to your TraceLoopLedger.arc56.json ABI[cite: 4]
ARC28_EVENTS = {
    get_selector("DeviceRegistered(string,string,uint64)"): {
        "name": "DeviceRegistered",
        "type": ABIType.from_string("(string,string,uint64)"),
        "event_type": "REGISTERED"
    },
    get_selector("DeviceVerified(string,address,string,uint64)"): {
        "name": "DeviceVerified",
        "type": ABIType.from_string("(string,address,string,uint64)"),
        "event_type": "VERIFIED"
    },
    get_selector("DeviceTransferred(string,string,address,uint64)"): {
        "name": "DeviceTransferred",
        "type": ABIType.from_string("(string,string,address,uint64)"),
        "event_type": "TRANSFERRED"
    },
    get_selector("HardwareChangeDeclared(string,string,uint64)"): {
        "name": "HardwareChangeDeclared",
        "type": ABIType.from_string("(string,string,uint64)"),
        "event_type": "VERIFIED" # Maps to VERIFIED state, but invalidates stamp[cite: 6]
    },
    get_selector("DisputeRaised(string,address,uint64,uint64)"): {
        "name": "DisputeRaised",
        "type": ABIType.from_string("(string,address,uint64,uint64)"),
        "event_type": "DISPUTED"
    },
    get_selector("DisputeResolved(string,uint64,uint64)"): {
        "name": "DisputeResolved",
        "type": ABIType.from_string("(string,uint64,uint64)"),
        "event_type": "DISPUTE_RESOLVED" 
    },
    get_selector("DeviceRecycled(string,address,uint64)"): {
        "name": "DeviceRecycled",
        "type": ABIType.from_string("(string,address,uint64)"),
        "event_type": "RECYCLED"
    },
    get_selector("DeviceExported(string,uint64)"): {
        "name": "DeviceExported",
        "type": ABIType.from_string("(string,uint64)"),
        "event_type": "EXPORTED"
    }
}

class SubscriberDaemon:
    def __init__(self):
        self.indexer = indexer.IndexerClient(INDEXER_TOKEN, INDEXER_URL)
        self.app_id = APP_ID
        self.poll_interval = 5  # seconds
        self.last_round = self._load_watermark()

    def _load_watermark(self) -> int:
        """Loads the last processed block round to prevent duplicate syncing."""
        if os.path.exists("subscriber_watermark.txt"):
            with open("subscriber_watermark.txt", "r") as f:
                return int(f.read().strip())
        return 0

    def _save_watermark(self, round_num: int):
        """Persists the watermark after processing a block."""
        with open("subscriber_watermark.txt", "w") as f:
            f.write(str(round_num))
        self.last_round = round_num

    def process_logs(self, txn: dict):
        """Extracts and decodes ARC-28 logs from an Algorand transaction."""
        logs = txn.get("logs", [])
        for b64_log in logs:
            log_bytes = base64.b64decode(b64_log)
            if len(log_bytes) < 4:
                continue
                
            # The first 4 bytes of an ARC-28 log represent the event selector[cite: 4]
            selector = log_bytes[:4]
            if selector in ARC28_EVENTS:
                event_def = ARC28_EVENTS[selector]
                try:
                    # Decode the remaining bytes using the matching ABI type[cite: 4]
                    decoded = event_def["type"].decode(log_bytes[4:])
                    self.mirror_to_database(txn, event_def, decoded)
                except Exception as e:
                    logger.error(f"Failed to decode {event_def['name']}: {e}")

    def mirror_to_database(self, txn: dict, event_def: dict, decoded: list):
        """
        Maps the decoded blockchain event to the PostgreSQL database fields[cite: 5].
        """
        device_id = decoded[0]
        event_name = event_def["name"]
        
        # Dynamically build the JSONB metadata payload based on the specific event[cite: 4, 5]
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
        
        # --- DATABASE INSERTION LOGIC ---
        # Instruct your Pillar 2 team to uncomment and wire this to their SQLAlchemy Session[cite: 5]
        #
        # db = SessionLocal()
        # try:
        #     # Idempotency check: Don't insert the same tx_hash twice
        #     exists = db.query(LedgerEvent).filter_by(tx_hash=txn["id"]).first()
        #     if not exists:
        #         new_event = LedgerEvent(
        #             id=uuid.uuid4(),
        #             device_id=device_id,
        #             event_type=event_def["event_type"],
        #             actor_type="ADMIN", # Backend should map sender address to User Role
        #             tx_hash=txn["id"],
        #             block_number=txn["confirmed-round"],
        #             chain_status="CONFIRMED",
        #             metadata=metadata,
        #             confirmed_at=datetime.utcnow()
        #         )
        #         db.add(new_event)
        #         db.commit()
        # except Exception as e:
        #     db.rollback()
        #     logger.error(f"DB Error: {e}")
        # finally:
        #     db.close()

    def start(self):
        logger.info(f"Starting Trace-Loop Subscriber Daemon for App ID: {self.app_id}")
        while True:
            try:
                # Query indexer for any app calls to our contract since the last watermark
                response = self.indexer.search_transactions(
                    application_id=self.app_id,
                    min_round=self.last_round + 1
                )
                
                transactions = response.get("transactions", [])
                if transactions:
                    # Sort transactions by confirmed round to process in strict chronological order
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
