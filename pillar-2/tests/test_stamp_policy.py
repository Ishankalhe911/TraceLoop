import pytest

from services.stamp_policy import effective_stamp_valid


@pytest.mark.parametrize("state", ["REGISTERED", "TRANSFERRED", "DISPUTED", "RECYCLED", "EXPORTED"])
def test_non_verified_states_have_no_active_transfer_stamp_even_if_raw_bit_is_true(state):
    assert effective_stamp_valid(state, True) is False
    assert effective_stamp_valid(state, False) is False


def test_verified_state_uses_the_actual_boolean_stamp_bit():
    assert effective_stamp_valid("VERIFIED", True) is True
    assert effective_stamp_valid("VERIFIED", False) is False
    assert effective_stamp_valid("VERIFIED", None) is None


def test_unknown_state_does_not_invent_a_stamp_value():
    assert effective_stamp_valid("SOURCE_UNAVAILABLE", None) is None
    assert effective_stamp_valid("SOURCE_UNAVAILABLE", True) is True


def test_enum_like_state_is_supported():
    class State:
        value = "TRANSFERRED"

    assert effective_stamp_valid(State(), True) is False
