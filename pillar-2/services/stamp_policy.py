"""Shared policy for whether a verification stamp is active for a new transfer.

The on-chain DeviceRecord currently retains its last ``stamp_valid`` bit after
some older lifecycle transitions. A stamp is only active for transfer purposes
while the device is in VERIFIED state. This helper exposes that effective
meaning without pretending that the raw on-chain bit was changed.

The contract source should also clear the stored bit on transfer and terminal
transitions. This compatibility policy is still needed while previously
confirmed TestNet records (and any existing deployment) retain the old bit.
"""
from __future__ import annotations

from typing import Any, Optional


_KNOWN_STATES_WITHOUT_ACTIVE_STAMP = {
    "REGISTERED",
    "TRANSFERRED",
    "DISPUTED",
    "RECYCLED",
    "EXPORTED",
}


def _state_text(state_label: Any) -> Optional[str]:
    if state_label is None:
        return None
    value = getattr(state_label, "value", state_label)
    try:
        text = str(value).strip().upper()
    except Exception:
        return None
    return text or None


def effective_stamp_valid(state_label: Any, raw_stamp_valid: Any) -> Optional[bool]:
    """Return whether the current stamp is active for a new transfer.

    - VERIFIED: use the raw stored flag (if available).
    - Any known non-VERIFIED lifecycle state: False, regardless of a stale
      raw bit retained by an older contract deployment.
    - Unknown state: return the raw boolean only if it is actually boolean;
      otherwise return None rather than inventing a value.

    This is a semantic view, not a mutation of PostgreSQL or on-chain state.
    """
    state = _state_text(state_label)
    if state == "VERIFIED":
        return raw_stamp_valid if isinstance(raw_stamp_valid, bool) else None
    if state in _KNOWN_STATES_WITHOUT_ACTIVE_STAMP:
        return False
    return raw_stamp_valid if isinstance(raw_stamp_valid, bool) else None
