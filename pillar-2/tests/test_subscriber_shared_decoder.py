"""Ensure the background subscriber and on-demand history reader share decoder logic."""
from services import arc28_event_decoder
from services import subscriber_service


def test_subscriber_uses_the_exact_shared_decoder_and_event_catalog():
    assert subscriber_service.ARC28_EVENTS is arc28_event_decoder.ARC28_EVENTS
    assert subscriber_service.decode_event_payload is arc28_event_decoder.decode_event_payload
    assert subscriber_service.get_selector is arc28_event_decoder.get_selector
