"""Shared, defensive decoder for TraceLoop's deployed ARC-28 lifecycle events.

This module contains only deterministic binary decoding. It performs no network
requests and does not access the database. Keep event signatures synchronized
with the deployed contract; changing a signature changes the selector.
"""
from __future__ import annotations

import base64
import struct
from typing import Any, Dict, List, Optional, Tuple

from algosdk.encoding import checksum, encode_address


def get_selector(signature: str) -> bytes:
    """Return the four-byte ARC-28 event selector (SHA-512/256 prefix)."""
    return checksum(signature.encode("utf-8"))[:4]


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


# Event name, argument types, and safe normalized event type. Signatures must
# match the declarations in the deployed smart contract exactly.
ARC28_EVENTS: Dict[bytes, Dict[str, Any]] = {
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
    """Decode known ARC-28 tuple payload after its four-byte selector."""
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
            if offset < head_size or offset >= len(payload):
                raise ValueError("ARC-4 string offset is outside the event payload")
            value, _ = decode_arc4_string(payload, offset)
            values.append(value)
        else:
            raise ValueError(f"Unsupported ARC-28 event argument type: {arg_type}")
    return values


def decode_arc28_log(encoded_log: str) -> Optional[Dict[str, Any]]:
    """Decode a base64 Indexer log; return None for unknown/non-ARC-28 logs.

    A malformed log with a recognized selector raises ValueError, allowing the
    caller to mark history as incomplete instead of silently omitting a fact.
    """
    try:
        raw = base64.b64decode(encoded_log, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("Indexer log is not valid base64") from exc
    if len(raw) < 4:
        return None
    event_def = ARC28_EVENTS.get(raw[:4])
    if event_def is None:
        return None
    decoded = decode_event_payload(event_def["args"], raw[4:])
    return {
        "name": event_def["name"],
        "event_type": event_def["event_type"],
        "args": decoded,
    }
