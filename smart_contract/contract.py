"""
TRACE-LOOP — Ledger Smart Contract
Pillar 1 — Algorand / PuyaPy (algopy)

DESIGN DECISIONS THIS CONTRACT IMPLEMENTS (locked in team discussion):

1. ACCOUNT MODEL
   - One shared "operator" account signs all backend-mediated actions
     (register, hardware change, dispute raise, export) on behalf of
     FIRST_BUYER / BUYER / RESELLER / ADMIN. Role validation for these
     happens off-chain in FastAPI middleware (JWT + role check) before
     the operator ever signs a transaction.
   - Each VERIFIABLE (brand-authorized service center) gets its OWN
     Algorand account, managed custodially by the backend. This is the
     one place we DO want on-chain attribution — a stamp must be
     provably traceable to a specific verifier, because verifier
     accountability is the core trust anchor of the system. This is
     achieved for free via Txn.sender — no extra "actor_hash" field
     needed, since the verifier's own account IS the attribution.
   - RECYCLER accounts are whitelisted the same way as verifiers
     (separate registry) but are few in number and terminal-only.
   - ADMIN = contract creator (Global.creator_address). Manages both
     whitelists and can force-resolve disputes / mark exports.

2. ENFORCEMENT, NOT JUST TRACING
   - The contract is the single source of truth for what state
     transitions are LEGAL. Illegal transitions are rejected by the
     contract itself (assert + revert) — not just flagged after the
     fact. Examples: no buyer-to-buyer transfer, no transfer without a
     valid stamp, no action after RECYCLED/EXPORTED (terminal states).
   - What the contract deliberately does NOT do: stop a device from
     being sold off-platform entirely (impossible for any system to
     prevent). What it DOES do: make re-entry into the verified/white
     market impossible without a fresh, attributable verification that
     will expose any gap or mismatch in history.

3. STAMP VALIDITY
   - stamp_valid is a boolean gate, not a full sub-state. It goes
     False the moment a hardware change is declared, and can only be
     set True again by a whitelisted VERIFIABLE. transfer_device
     requires stamp_valid == True, so the "re-verify before you can
     move it again" rule is enforced purely through this flag plus the
     state machine below.

4. HASH STORAGE
   - owner_hash / config_hash are stored as arc4.String (hex-encoded
     SHA-256 produced off-chain). We deliberately did NOT use
     StaticArray[Byte, 32] — the contract never needs to do on-chain
     equality/comparison of these hashes (that comparison is a human
     job done by the verifier during physical inspection), so the
     extra complexity of fixed-length byte arrays buys us nothing for
     V1. This is a simplification, not a limitation.

STATE MACHINE (mirrors Requirements & SOP doc, Section 2):

    (none) --register_device--> REGISTERED
    REGISTERED --verify_device--> VERIFIED
    VERIFIED --transfer_device--> TRANSFERRED   [requires stamp_valid]
    TRANSFERRED --verify_device--> VERIFIED     [loop continues]
    VERIFIED --declare_hardware_change--> VERIFIED (stamp_valid -> False)
    TRANSFERRED --mark_recycled--> RECYCLED     [terminal, recycler only]
    (any non-terminal) --raise_dispute--> DISPUTED
    DISPUTED --resolve_dispute--> REGISTERED | VERIFIED | TRANSFERRED
    (any non-terminal) --mark_exported--> EXPORTED [terminal, admin only]

BLOCKED AT PROTOCOL LEVEL (contract asserts against these):
    - REGISTERED -> TRANSFERRED directly (skip verification)
    - TRANSFERRED -> TRANSFERRED directly (buyer-to-buyer)
    - VERIFIED -> RECYCLED directly (must transfer to recycler first)
    - Any action on RECYCLED or EXPORTED (terminal states)
    - transfer_device when stamp_valid == False

NOTE ON SYNTAX: written against algopy / PuyaPy as of late-2024/2025
API surface (ARC4Contract, BoxMap, arc4.emit for ARC-28 events). Since
this API evolves between algokit-utils / puya releases, run
`algokit compile py` immediately after dropping this in and fix any
signature drift against your installed version before writing tests.
"""

import typing
from algopy import (
    ARC4Contract,
    Box,
    BoxMap,
    Global,
    Txn,
    UInt64,
    arc4,
)


# ---------------------------------------------------------------------------
# Device lifecycle state constants
# ---------------------------------------------------------------------------

STATE_NONE = 0          # device_id not registered yet
STATE_REGISTERED = 1
STATE_VERIFIED = 2
STATE_TRANSFERRED = 3
STATE_DISPUTED = 4
STATE_RECYCLED = 5      # terminal
STATE_EXPORTED = 6      # terminal


# ---------------------------------------------------------------------------
# ARC-4 structs — on-chain record + ARC-28 style events
# ---------------------------------------------------------------------------

class DeviceRecord(arc4.Struct):
    """Box value stored per device_id."""
    state: arc4.UInt64
    owner_hash: arc4.String          # SHA-256 hex of current owner identity
    config_hash: arc4.String         # SHA-256 hex of current declared config
    stamp_valid: arc4.Bool
    last_verifier: arc4.Address      # zero address until first verify
    updated_at: arc4.UInt64          # Global.latest_timestamp at last write


class DeviceRegistered(arc4.Struct):
    device_id: arc4.String
    owner_hash: arc4.String
    timestamp: arc4.UInt64


class DeviceVerified(arc4.Struct):
    device_id: arc4.String
    verifier: arc4.Address
    config_hash: arc4.String
    timestamp: arc4.UInt64


class DeviceTransferred(arc4.Struct):
    device_id: arc4.String
    new_owner_hash: arc4.String
    verifier: arc4.Address
    timestamp: arc4.UInt64


class HardwareChangeDeclared(arc4.Struct):
    device_id: arc4.String
    new_config_hash: arc4.String
    timestamp: arc4.UInt64


class DisputeRaised(arc4.Struct):
    device_id: arc4.String
    raised_by: arc4.Address
    dispute_type: arc4.UInt64
    timestamp: arc4.UInt64


class DisputeResolved(arc4.Struct):
    device_id: arc4.String
    resolved_state: arc4.UInt64
    timestamp: arc4.UInt64


class DeviceRecycled(arc4.Struct):
    device_id: arc4.String
    recycler: arc4.Address
    timestamp: arc4.UInt64


class DeviceExported(arc4.Struct):
    device_id: arc4.String
    timestamp: arc4.UInt64


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------

class TraceLoopLedger(ARC4Contract):

    def __init__(self) -> None:
        # Backend operator account — signs on behalf of FIRST_BUYER /
        # BUYER / RESELLER / ADMIN actions. Role checks for those
        # happen off-chain; the contract only checks "is this the
        # operator" for the methods those roles use.
        self.operator: Box[arc4.Address] = Box(arc4.Address, key=b"operator")

        # Verifier whitelist — one entry per authorized service center.
        # Value is unused (Bool True marks presence); absence = not
        # authorized.
        self.verifiers: BoxMap[arc4.Address, arc4.Bool] = BoxMap(
            arc4.Address, arc4.Bool, key_prefix=b"verifier_"
        )

        # Recycler whitelist — same pattern, small set.
        self.recyclers: BoxMap[arc4.Address, arc4.Bool] = BoxMap(
            arc4.Address, arc4.Bool, key_prefix=b"recycler_"
        )

        # Core device state, keyed by device_id string
        # (format: TL-{BRAND_CODE}-{SERIAL}).
        self.devices: BoxMap[arc4.String, DeviceRecord] = BoxMap(
            arc4.String, DeviceRecord, key_prefix=b"device_"
        )

    # -----------------------------------------------------------------
    # Admin setup — creator only
    # -----------------------------------------------------------------

    @arc4.abimethod
    def set_operator(self, operator: arc4.Address) -> None:
        """One-time (or rotate-time) assignment of the backend operator
        account. Admin only."""
        assert Txn.sender == Global.creator_address, "only admin"
        self.operator.value = operator

    @arc4.abimethod
    def add_verifier(self, verifier: arc4.Address) -> None:
        """Whitelist a new authorized service center account.
        Admin only — reflects in-person onboarding, no self-serve."""
        assert Txn.sender == Global.creator_address, "only admin"
        self.verifiers[verifier] = arc4.Bool(True)  # noqa: FBT003

    @arc4.abimethod
    def remove_verifier(self, verifier: arc4.Address) -> None:
        """Revoke a verifier's authorization (e.g. reputation dropped
        below threshold, or brand authorization lost). Admin only."""
        assert Txn.sender == Global.creator_address, "only admin"
        assert verifier in self.verifiers, "verifier not found"
        del self.verifiers[verifier]

    @arc4.abimethod
    def add_recycler(self, recycler: arc4.Address) -> None:
        """Whitelist a CPCB-registered recycler account. Admin only."""
        assert Txn.sender == Global.creator_address, "only admin"
        self.recyclers[recycler] = arc4.Bool(True)  # noqa: FBT003
    @arc4.abimethod
    def remove_recycler(self, recycler: arc4.Address) -> None:
        """Revoke a recycler's authorization. Admin only."""
        assert Txn.sender == Global.creator_address, "only admin"
        assert recycler in self.recyclers, "recycler not found"
        del self.recyclers[recycler]

    # -----------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------

    def _assert_operator(self) -> None:
        assert Txn.sender == self.operator.value.native, "only operator"

    def _assert_verifier(self) -> None:
        assert arc4.Address(Txn.sender) in self.verifiers, "not an authorized verifier"

    def _assert_recycler(self) -> None:
        assert arc4.Address(Txn.sender) in self.recyclers, "not an authorized recycler"

    def _assert_not_terminal(self, state: UInt64) -> None:
        assert state != STATE_RECYCLED, "device is recycled — terminal"
        assert state != STATE_EXPORTED, "device is exported — terminal"

    # -----------------------------------------------------------------
    # 1. Register — FIRST_BUYER, via operator
    # -----------------------------------------------------------------

    @arc4.abimethod
    def register_device(
        self,
        device_id: arc4.String,
        owner_hash: arc4.String,
        config_hash: arc4.String,
    ) -> None:
        self._assert_operator()
        assert device_id not in self.devices, "device already registered"

        record = DeviceRecord(
            state=arc4.UInt64(STATE_REGISTERED),
            owner_hash=owner_hash,
            config_hash=config_hash,
            stamp_valid=arc4.Bool(False),  # noqa: FBT003 — not verified yet
            last_verifier=arc4.Address(Global.zero_address),
            updated_at=arc4.UInt64(Global.latest_timestamp),
        )
        self.devices[device_id] = record.copy()

        arc4.emit(
            DeviceRegistered(
                device_id=device_id,
                owner_hash=owner_hash,
                timestamp=arc4.UInt64(Global.latest_timestamp),
            )
        )

    # -----------------------------------------------------------------
    # 2. Verify / stamp — VERIFIABLE only
    #    Legal from REGISTERED or TRANSFERRED (re-verification loop)
    # -----------------------------------------------------------------

    @arc4.abimethod
    def verify_device(
        self,
        device_id: arc4.String,
        confirmed_config_hash: arc4.String,
    ) -> None:
        self._assert_verifier()
        assert device_id in self.devices, "device not registered"

        record = self.devices[device_id].copy()
        state = record.state.native
        self._assert_not_terminal(state)
        assert (
            state == STATE_REGISTERED
            or state == STATE_TRANSFERRED
            or (state == STATE_VERIFIED and not record.stamp_valid.native)
        ), "verify only allowed from REGISTERED, TRANSFERRED, or VERIFIED with invalid stamp"

        record.state = arc4.UInt64(STATE_VERIFIED)
        record.config_hash = confirmed_config_hash
        record.stamp_valid = arc4.Bool(True)  # noqa: FBT003
        record.last_verifier = arc4.Address(Txn.sender)
        record.updated_at = arc4.UInt64(Global.latest_timestamp)
        self.devices[device_id] = record.copy()

        arc4.emit(
            DeviceVerified(
                device_id=device_id,
                verifier=arc4.Address(Txn.sender),
                config_hash=confirmed_config_hash,
                timestamp=arc4.UInt64(Global.latest_timestamp),
            )
        )

    # -----------------------------------------------------------------
    # 3. Transfer — VERIFIABLE only, requires VERIFIED + stamp_valid
    #    This is the protocol-level block on buyer-to-buyer transfer:
    #    a buyer can never call this directly, and it can only fire
    #    immediately after a fresh verify_device call.
    # -----------------------------------------------------------------

    @arc4.abimethod
    def transfer_device(
        self,
        device_id: arc4.String,
        new_owner_hash: arc4.String,
    ) -> None:
        self._assert_verifier()
        assert device_id in self.devices, "device not registered"

        record = self.devices[device_id].copy()
        state = record.state.native
        self._assert_not_terminal(state)
        assert state == STATE_VERIFIED, "transfer only allowed from VERIFIED"
        assert record.stamp_valid.native, "stamp not valid — re-verify first"

        record.state = arc4.UInt64(STATE_TRANSFERRED)
        record.owner_hash = new_owner_hash
        record.updated_at = arc4.UInt64(Global.latest_timestamp)
        self.devices[device_id] = record.copy()

        arc4.emit(
            DeviceTransferred(
                device_id=device_id,
                new_owner_hash=new_owner_hash,
                verifier=arc4.Address(Txn.sender),
                timestamp=arc4.UInt64(Global.latest_timestamp),
            )
        )

    # -----------------------------------------------------------------
    # 4. Hardware change declaration — operator only (current owner,
    #    validated off-chain), invalidates stamp
    # -----------------------------------------------------------------

    @arc4.abimethod
    def declare_hardware_change(
        self,
        device_id: arc4.String,
        new_config_hash: arc4.String,
    ) -> None:
        self._assert_operator()
        assert device_id in self.devices, "device not registered"

        record = self.devices[device_id].copy()
        state = record.state.native
        self._assert_not_terminal(state)
        # Disputed devices cannot declare hardware changes — dispute
        # must be resolved first.
        assert state != STATE_DISPUTED, (
            "cannot declare hardware change while disputed"
        )
        # Valid in any active ownership state: before first verify
        # (REGISTERED), after verify (VERIFIED), or after transfer
        # (TRANSFERRED). Per SOP 1.2, FIRST_BUYER is explicitly
        # permitted to declare changes before initial verification.
        assert (
            state == STATE_REGISTERED
            or state == STATE_VERIFIED
            or state == STATE_TRANSFERRED
        ), "hardware change only allowed in REGISTERED, VERIFIED, or TRANSFERRED"

        record.config_hash = new_config_hash
        record.stamp_valid = arc4.Bool(False)  # noqa: FBT003 — force re-verify
        record.updated_at = arc4.UInt64(Global.latest_timestamp)
        self.devices[device_id] = record.copy()

        arc4.emit(
            HardwareChangeDeclared(
                device_id=device_id,
                new_config_hash=new_config_hash,
                timestamp=arc4.UInt64(Global.latest_timestamp),
            )
        )

    # -----------------------------------------------------------------
    # 5. Dispute — operator (on behalf of any role) OR verifier
    #    Callable from any non-terminal state.
    # -----------------------------------------------------------------

    @arc4.abimethod
    def raise_dispute(
        self,
        device_id: arc4.String,
        dispute_type: arc4.UInt64,
    ) -> None:
        is_operator = Txn.sender == self.operator.value.native
        is_verifier = arc4.Address(Txn.sender) in self.verifiers
        assert is_operator or is_verifier, "not authorized to raise dispute"
        assert device_id in self.devices, "device not registered"

        record = self.devices[device_id].copy()
        state = record.state.native
        self._assert_not_terminal(state)

        record.state = arc4.UInt64(STATE_DISPUTED)
        record.stamp_valid = arc4.Bool(False)  # noqa: FBT003
        record.updated_at = arc4.UInt64(Global.latest_timestamp)
        self.devices[device_id] = record.copy()

        arc4.emit(
            DisputeRaised(
                device_id=device_id,
                raised_by=arc4.Address(Txn.sender),
                dispute_type=dispute_type,
                timestamp=arc4.UInt64(Global.latest_timestamp),
            )
        )

    @arc4.abimethod
    def resolve_dispute(
        self,
        device_id: arc4.String,
        resolved_state: arc4.UInt64,
    ) -> None:
        """Admin only. resolved_state must be REGISTERED, VERIFIED, or
        TRANSFERRED — never a terminal state (use mark_recycled /
        mark_exported for that) and never DISPUTED again (no-op)."""
        assert Txn.sender == Global.creator_address, "only admin"
        assert device_id in self.devices, "device not registered"

        record = self.devices[device_id].copy()
        assert record.state.native == STATE_DISPUTED, "device not disputed"

        target = resolved_state.native
        assert (
            target == STATE_REGISTERED
            or target == STATE_VERIFIED
            or target == STATE_TRANSFERRED
        ), "must resolve into REGISTERED, VERIFIED, or TRANSFERRED"

        record.state = arc4.UInt64(target)
        # Always force re-verification after a dispute resolution,
        # regardless of which state it resolves into.
        record.stamp_valid = arc4.Bool(False)  # noqa: FBT003
        record.updated_at = arc4.UInt64(Global.latest_timestamp)
        self.devices[device_id] = record.copy()

        arc4.emit(
            DisputeResolved(
                device_id=device_id,
                resolved_state=resolved_state,
                timestamp=arc4.UInt64(Global.latest_timestamp),
            )
        )

    # -----------------------------------------------------------------
    # 6. Recycle — RECYCLER only, terminal
    #    Blocked at protocol level: VERIFIED -> RECYCLED directly is
    #    NOT allowed — device must be TRANSFERRED to the recycler first
    #    (i.e. go through a verifier hand-off) before it can be marked
    #    recycled.
    # -----------------------------------------------------------------

    @arc4.abimethod
    def mark_recycled(self, device_id: arc4.String) -> None:
        self._assert_recycler()
        assert device_id in self.devices, "device not registered"

        record = self.devices[device_id].copy()
        assert (
            record.state.native == STATE_TRANSFERRED
        ), "device must be freshly TRANSFERRED to a recycler to be recycled"

        record.state = arc4.UInt64(STATE_RECYCLED)
        record.updated_at = arc4.UInt64(Global.latest_timestamp)
        self.devices[device_id] = record.copy()

        arc4.emit(
            DeviceRecycled(
                device_id=device_id,
                recycler=arc4.Address(Txn.sender),
                timestamp=arc4.UInt64(Global.latest_timestamp),
            )
        )

    # -----------------------------------------------------------------
    # 7. Export — ADMIN only, terminal
    # -----------------------------------------------------------------

    @arc4.abimethod
    def mark_exported(self, device_id: arc4.String) -> None:
        assert Txn.sender == Global.creator_address, "only admin"
        assert device_id in self.devices, "device not registered"

        record = self.devices[device_id].copy()
        self._assert_not_terminal(record.state.native)

        record.state = arc4.UInt64(STATE_EXPORTED)
        record.updated_at = arc4.UInt64(Global.latest_timestamp)
        self.devices[device_id] = record.copy()

        arc4.emit(
            DeviceExported(
                device_id=device_id,
                timestamp=arc4.UInt64(Global.latest_timestamp),
            )
        )

    # -----------------------------------------------------------------
    # Read-only helpers — called by ledger_service before mutating
    # calls, and exposed to any active role for history/state lookups.
    # -----------------------------------------------------------------

    @arc4.abimethod(readonly=True)
    def get_device_state(self, device_id: arc4.String) -> DeviceRecord:
        assert device_id in self.devices, "device not registered"
        return self.devices[device_id].copy()

    @arc4.abimethod(readonly=True)
    def can_verify(self, device_id: arc4.String) -> arc4.Bool:
        if device_id not in self.devices:
            return arc4.Bool(False)  # noqa: FBT003
        record = self.devices[device_id].copy()
        state = record.state.native
        ok = (
            state == STATE_REGISTERED
            or state == STATE_TRANSFERRED
            or (state == STATE_VERIFIED and not record.stamp_valid.native)
        )
        return arc4.Bool(ok)

    @arc4.abimethod(readonly=True)
    def can_transfer(self, device_id: arc4.String) -> arc4.Bool:
        if device_id not in self.devices:
            return arc4.Bool(False)  # noqa: FBT003
        record = self.devices[device_id].copy()
        ok = (
            record.state.native == STATE_VERIFIED
            and record.stamp_valid.native
        )
        return arc4.Bool(ok)

    @arc4.abimethod(readonly=True)
    def can_recycle(self, device_id: arc4.String) -> arc4.Bool:
        if device_id not in self.devices:
            return arc4.Bool(False)  # noqa: FBT003
        record = self.devices[device_id].copy()
        ok = record.state.native == STATE_TRANSFERRED
        return arc4.Bool(ok)