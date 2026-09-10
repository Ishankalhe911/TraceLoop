from datetime import datetime
import hashlib


def create_ledger_entry(event_type, device_id, data):
    """
    Creates a simple tamper-evident ledger entry.
    """

    timestamp = datetime.utcnow().isoformat() + "Z"

    entry = {
        "event_type": event_type,
        "device_id": device_id,
        "timestamp": timestamp,
        "data": data
    }

    # Create a hash so the entry can be verified later
    raw_data = f"{event_type}|{device_id}|{timestamp}|{data}"
    entry["hash"] = hashlib.sha256(raw_data.encode()).hexdigest()

    return entry
