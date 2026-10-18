"""Tests for independent, paginated chain history retrieval."""
import base64
import struct

from services.arc28_event_decoder import get_selector
from services.chain_history_service import ChainHistoryService


def encode_registered(public_id: str, owner_hash: str = "owner", event_time: int = 1234) -> str:
    head_size = 12
    first = public_id.encode()
    second = owner_hash.encode()
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
    return base64.b64encode(
        get_selector("DeviceRegistered(string,string,uint64)") + payload
    ).decode()


class FakeIndexer:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def search_transactions(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("Unexpected Indexer page request")
        response = self.responses.pop(0)
        return response


def test_history_is_decoded_independently_and_filtered_to_device():
    target = "TL-DELL-AAAAAAAAAAAAAAAAAAAAAAAA"
    other = "TL-DELL-BBBBBBBBBBBBBBBBBBBBBBBB"
    indexer = FakeIndexer([{
        "current-round": 500100,
        "transactions": [
            {
                "id": "TARGET-TX",
                "confirmed-round": 500090,
                "intra-round-offset": 1,
                "round-time": 1800000000,
                "logs": [encode_registered(target)],
            },
            {
                "id": "OTHER-TX",
                "confirmed-round": 500091,
                "logs": [encode_registered(other)],
            },
        ],
    }])
    service = ChainHistoryService(indexer, app_id=771258340, start_round=50000000, page_size=100, max_pages=5)

    result = service.get_device_history(target, registration_round=500090)

    assert result["status"] == "AVAILABLE"
    assert result["complete"] is True
    assert result["history_from_round"] == 500090
    assert result["indexer_through_round"] == 500100
    assert len(result["events"]) == 1
    assert result["events"][0]["transaction_ref"] == "TARGET-TX"
    assert result["events"][0]["event_type"] == "REGISTERED"
    assert "owner" not in str(result["events"])
    assert indexer.calls[0]["application_id"] == 771258340
    assert indexer.calls[0]["min_round"] == 500090


def test_history_page_limit_is_reported_as_incomplete_not_empty_success():
    target = "TL-DELL-AAAAAAAAAAAAAAAAAAAAAAAA"
    pages = [
        {"current-round": 500100, "transactions": [], "next-token": "page-2"},
        {"current-round": 500100, "transactions": [], "next-token": "page-3"},
    ]
    service = ChainHistoryService(FakeIndexer(pages), app_id=771258340, page_size=10, max_pages=2)

    result = service.get_device_history(target)

    assert result["status"] == "AVAILABLE"
    assert result["complete"] is False
    assert result["failure_code"] == "HISTORY_PAGE_LIMIT_REACHED"


def test_repeated_history_lookup_uses_short_bounded_cache():
    target = "TL-DELL-CCCCCCCCCCCCCCCCCCCCCCCC"
    indexer = FakeIndexer([{"current-round": 500100, "transactions": []}])
    service = ChainHistoryService(
        indexer, app_id=771258340, start_round=50000000,
        page_size=10, max_pages=2,
    )

    first = service.get_device_history(target)
    second = service.get_device_history(target)

    assert first == second
    assert len(indexer.calls) == 1
