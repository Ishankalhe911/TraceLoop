"""Regression tests for the shared ARC-28 decoder."""
import base64
import struct

import pytest

from services.arc28_event_decoder import (
    ARC28_EVENTS,
    decode_arc28_log,
    decode_event_payload,
    get_selector,
)


def _encode_registered_log(public_id: str, owner_hash: str = "owner-hash", event_time: int = 12345) -> str:
    # DeviceRegistered(string,string,uint64): two offsets + a uint64 in the head.
    head_size = 2 + 2 + 8
    first = public_id.encode("utf-8")
    second = owner_hash.encode("utf-8")
    first_tail = len(first).to_bytes(2, "big") + first
    second_offset = head_size + len(first_tail)
    second_tail = len(second).to_bytes(2, "big") + second
    payload = (
        struct.pack(">H", head_size)
        + struct.pack(">H", second_offset)
        + struct.pack(">Q", event_time)
        + first_tail
        + second_tail
    )
    selector = get_selector("DeviceRegistered(string,string,uint64)")
    return base64.b64encode(selector + payload).decode("ascii")


def test_shared_decoder_decodes_known_event_and_arguments():
    public_id = "TL-DELL-AAAAAAAAAAAAAAAAAAAAAAAA"
    decoded = decode_arc28_log(_encode_registered_log(public_id))

    assert decoded is not None
    assert decoded["name"] == "DeviceRegistered"
    assert decoded["event_type"] == "REGISTERED"
    assert decoded["args"] == [public_id, "owner-hash", 12345]
    assert get_selector("DeviceRegistered(string,string,uint64)") in ARC28_EVENTS


def test_unknown_log_is_ignored_but_known_malformed_event_fails_closed():
    assert decode_arc28_log(base64.b64encode(b"not-an-event").decode("ascii")) is None
    selector = get_selector("DeviceRegistered(string,string,uint64)")
    with pytest.raises(ValueError):
        decode_arc28_log(base64.b64encode(selector + b"\x00").decode("ascii"))


def test_tuple_decoder_rejects_offsets_outside_payload():
    # Two string offsets claim a tail begins outside the available bytes.
    with pytest.raises(ValueError):
        decode_event_payload(["string", "string", "uint64"], b"\xff\xff\xff\xff" + b"\x00" * 8)
