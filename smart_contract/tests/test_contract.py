"""
test_contract.py
----------------
Full test suite for TraceLoopLedger (App ID 770956001) on Algorand TestNet.

Prerequisites
─────────────
1. Run setup_test_accounts.py first to generate funded accounts.
2. Fill in ADMIN_MNEMONIC in pillar-1/.env.test with your deployer mnemonic.
3. Install deps:
     pip install py-algorand-sdk pytest python-dotenv

Run
───
  cd pillar-1
  pytest smart_contract/tests/test_contract.py -v

Coverage
────────
  Admin / setup     : set_operator, add_verifier, remove_verifier, add_recycler
  Device lifecycle  : register_device, verify_device, transfer_device,
                      declare_hardware_change, mark_recycled, mark_exported
  Disputes          : raise_dispute, resolve_dispute
  Read-only queries : get_device_state, can_verify, can_transfer, can_recycle
  Negative paths    : wrong role, wrong state, terminal state, double-register,
                      bad resolved_state, hardware change while disputed

State constants (must match contract)
──────────────────────────────────────
  REGISTERED = 1
  VERIFIED   = 2
  TRANSFERRED = 3
  DISPUTED   = 4
  RECYCLED   = 5
  EXPORTED   = 6
"""

import os
import time
import hashlib
import pytest

from algosdk import account, mnemonic, transaction
from algosdk.v2client import algod
from algosdk.atomic_transaction_composer import (
    AtomicTransactionComposer,
    AccountTransactionSigner,
    TransactionWithSigner,
)
from algosdk.abi import Contract, Method
from algosdk.encoding import decode_address
import json
from dotenv import load_dotenv
from pathlib import Path

# ── Load env ──────────────────────────────────────────────────────────────────

# pillar-1/.env  →  parents[2] from test_contract.py
# test_contract.py → parents[0]=tests, [1]=smart_contract, [2]=pillar-1
ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=True)

APP_ID      = int(os.environ["APP_ID"])
ALGOD_URL   = os.environ.get("ALGOD_URL", "https://testnet-api.algonode.cloud")
ALGOD_TOKEN = os.environ.get("ALGOD_TOKEN", "")

def _load_account(role: str):
    mn = os.environ[f"{role}_MNEMONIC"]
    pk = mnemonic.to_private_key(mn)
    addr = account.address_from_private_key(pk)
    return pk, addr

# ── Constants ─────────────────────────────────────────────────────────────────

STATE_REGISTERED  = 1
STATE_VERIFIED    = 2
STATE_TRANSFERRED = 3
STATE_DISPUTED    = 4
STATE_RECYCLED    = 5
STATE_EXPORTED    = 6

DISPUTE_TYPE_MISREPRESENTED = 1  # arbitrary uint64 — contract doesn't validate value

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def client():
    return algod.AlgodClient(ALGOD_TOKEN, ALGOD_URL)


@pytest.fixture(scope="session")
def admin_acct():
    pk, addr = _load_account("ADMIN")
    return pk, addr


@pytest.fixture(scope="session")
def operator_acct():
    pk, addr = _load_account("OPERATOR")
    return pk, addr


@pytest.fixture(scope="session")
def verifier_acct():
    pk, addr = _load_account("VERIFIER")
    return pk, addr


@pytest.fixture(scope="session")
def recycler_acct():
    pk, addr = _load_account("RECYCLER")
    return pk, addr


@pytest.fixture(scope="session")
def buyer_acct():
    pk, addr = _load_account("BUYER")
    return pk, addr


@pytest.fixture(scope="session")
def contract(client):
    """Load ABI from the compiled artifact."""
    artifact_path = Path(__file__).resolve().parents[1] / "artifacts" / "contract.json"
    with open(artifact_path) as f:
        raw = json.load(f)
    return Contract.from_json(json.dumps({
        "name": raw["name"],
        "methods": raw["methods"],
        "desc": raw.get("desc", ""),
    }))


# ── Helpers ───────────────────────────────────────────────────────────────────

def sp(client):
    """Fresh suggested params with flat fee."""
    params = client.suggested_params()
    params.flat_fee = True
    params.fee = 2_000   # 2x min — boxes add inner ops
    return params


def signer(private_key: str) -> AccountTransactionSigner:
    return AccountTransactionSigner(private_key)


def box_ref(name: bytes) -> list:
    return [{"app": APP_ID, "name": name}]


def device_box(device_id: str) -> bytes:
    id_bytes = device_id.encode("utf-8")
    # ABI string encoding requires a 2-byte length prefix before the string content
    length_prefix = len(id_bytes).to_bytes(2, "big")
    return b"device_" + length_prefix + id_bytes

def verifier_box(address: str) -> bytes:
    return b"verifier_" + decode_address(address)


def recycler_box(address: str) -> bytes:
    return b"recycler_" + decode_address(address)


def operator_box() -> bytes:
    return b"operator"


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def call(client, contract, method_name: str, sender_pk: str, sender_addr: str,
         args: list, boxes: list, note: str = "") -> dict:
    """
    Build and submit an ABI method call via AtomicTransactionComposer.
    Returns the confirmed transaction result dict.
    """
    atc = AtomicTransactionComposer()
    method: Method = contract.get_method_by_name(method_name)
    atc.add_method_call(
        app_id=APP_ID,
        method=method,
        sender=sender_addr,
        sp=sp(client),
        signer=signer(sender_pk),
        method_args=args,
        boxes=boxes,
        note=note.encode() if note else None,
    )
    result = atc.execute(client, 4)
    return result


def call_expect_fail(client, contract, method_name: str, sender_pk: str,
                     sender_addr: str, args: list, boxes: list) -> str:
    """
    Expect the call to fail (contract assert / revert).
    Returns the error message string for optional assertion.
    Raises AssertionError if the call SUCCEEDS unexpectedly.
    """
    try:
        call(client, contract, method_name, sender_pk, sender_addr, args, boxes)
        raise AssertionError(f"{method_name} should have failed but succeeded")
    except AssertionError:
        raise   # re-raise our own sentinel — don't swallow it
    except Exception as e:
        return str(e)


def get_device_state(client, contract, device_id: str) -> dict:
    """
    Call get_device_state via execute.
    Returns dict with keys: state, owner_hash, config_hash, stamp_valid,
                            last_verifier, updated_at
    """
    # LOAD FUNDED WALLET DIRECTLY FROM ENV
    pk, addr = _load_account("ADMIN")
    
    atc = AtomicTransactionComposer()
    method = contract.get_method_by_name("get_device_state")
    atc.add_method_call(
        app_id=APP_ID,
        method=method,
        sender=addr,
        sp=sp(client),
        signer=AccountTransactionSigner(pk),
        method_args=[device_id],
        boxes=[[APP_ID, device_box(device_id)]],
    )
    result = atc.execute(client, 4)
    ret = result.abi_results[0].return_value
    
    return {
        "state":         ret[0],
        "owner_hash":    ret[1],
        "config_hash":   ret[2],
        "stamp_valid":   ret[3],
        "last_verifier": ret[4],
        "updated_at":    ret[5],
    }


def call_bool_readonly(client, contract, method_name: str, device_id: str) -> bool:
    """Generic helper for can_verify / can_transfer / can_recycle."""
    # LOAD FUNDED WALLET DIRECTLY FROM ENV
    pk, addr = _load_account("ADMIN")
    
    atc = AtomicTransactionComposer()
    method = contract.get_method_by_name(method_name)
    atc.add_method_call(
        app_id=APP_ID,
        method=method,
        sender=addr,
        sp=sp(client),
        signer=AccountTransactionSigner(pk),
        method_args=[device_id],
        boxes=[[APP_ID, device_box(device_id)]],
    )
    result = atc.execute(client, 4)
    return bool(result.abi_results[0].return_value)

def unique_device_id(suffix: str = "") -> str:
    """Generate a unique device ID for each test to avoid state collisions."""
    ts = str(int(time.time() * 1000))[-6:]
    return f"TL-DL-TEST{ts}{suffix}"


# ── Section 1: Admin / Setup ──────────────────────────────────────────────────

class TestAdminSetup:

    def test_set_operator(self, client, contract, admin_acct, operator_acct):
        """Admin can set the operator account."""
        pk, addr = admin_acct
        op_addr = operator_acct[1]
        result = call(
            client, contract, "set_operator",
            pk, addr,
            args=[op_addr],
            boxes=[[APP_ID, operator_box()]],
        )
        assert result.confirmed_round > 0

    def test_set_operator_not_admin_fails(self, client, contract, operator_acct):
        """Non-admin cannot call set_operator."""
        pk, addr = operator_acct
        err = call_expect_fail(
            client, contract, "set_operator",
            pk, addr,
            args=[addr],
            boxes=[[APP_ID, operator_box()]],
        )
        assert "only admin" in err.lower() or "assert" in err.lower()

    def test_add_verifier(self, client, contract, admin_acct, verifier_acct):
        """Admin can whitelist a verifier."""
        pk, addr = admin_acct
        v_addr = verifier_acct[1]
        result = call(
            client, contract, "add_verifier",
            pk, addr,
            args=[v_addr],
            boxes=[[APP_ID, verifier_box(v_addr)]],
        )
        assert result.confirmed_round > 0

    def test_add_verifier_not_admin_fails(self, client, contract, operator_acct, verifier_acct):
        """Non-admin cannot add a verifier."""
        pk, addr = operator_acct
        v_addr = verifier_acct[1]
        err = call_expect_fail(
            client, contract, "add_verifier",
            pk, addr,
            args=[v_addr],
            boxes=[[APP_ID, verifier_box(v_addr)]],
        )
        assert "only admin" in err.lower() or "assert" in err.lower()

    def test_add_recycler(self, client, contract, admin_acct, recycler_acct):
        """Admin can whitelist a recycler."""
        pk, addr = admin_acct
        r_addr = recycler_acct[1]
        result = call(
            client, contract, "add_recycler",
            pk, addr,
            args=[r_addr],
            boxes=[[APP_ID, recycler_box(r_addr)]],
        )
        assert result.confirmed_round > 0

    def test_add_recycler_not_admin_fails(self, client, contract, operator_acct, recycler_acct):
        """Non-admin cannot add a recycler."""
        pk, addr = operator_acct
        r_addr = recycler_acct[1]
        err = call_expect_fail(
            client, contract, "add_recycler",
            pk, addr,
            args=[r_addr],
            boxes=[[APP_ID, recycler_box(r_addr)]],
        )
        assert "only admin" in err.lower() or "assert" in err.lower()
    
    def test_remove_recycler_then_re_add(self, client, contract, admin_acct, buyer_acct):
        """Admin can remove a recycler. Then re-add works."""
        pk, addr = admin_acct
        temp_addr = buyer_acct[1]

        # Add first
        call(client, contract, "add_recycler", pk, addr,
             args=[temp_addr], boxes=[[APP_ID, recycler_box(temp_addr)]])

        # Remove — should succeed
        result = call(client, contract, "remove_recycler", pk, addr,
                      args=[temp_addr], boxes=[[APP_ID, recycler_box(temp_addr)]])
        assert result.confirmed_round > 0

        # Remove again — should fail (not found)
        err = call_expect_fail(client, contract, "remove_recycler", pk, addr,
                               args=[temp_addr], boxes=[[APP_ID, recycler_box(temp_addr)]])
        assert "recycler not found" in err.lower() or "assert" in err.lower()

        # Re-add
        call(client, contract, "add_recycler", pk, addr,
             args=[temp_addr], boxes=[[APP_ID, recycler_box(temp_addr)]])
    def test_remove_verifier_then_re_add(self, client, contract, admin_acct, buyer_acct):
        """
        Admin can remove a verifier. Removing a non-existent verifier fails.
        Then re-add works.
        """
        pk, addr = admin_acct
        # Use buyer as a temp verifier to not disturb the main verifier
        temp_addr = buyer_acct[1]

        # Add first
        call(client, contract, "add_verifier", pk, addr,
             args=[temp_addr], boxes=[[APP_ID, verifier_box(temp_addr)]])

        # Remove — should succeed
        result = call(client, contract, "remove_verifier", pk, addr,
                      args=[temp_addr], boxes=[[APP_ID, verifier_box(temp_addr)]])
        assert result.confirmed_round > 0

        # Remove again — should fail (not found)
        err = call_expect_fail(client, contract, "remove_verifier", pk, addr,
                               args=[temp_addr], boxes=[[APP_ID, verifier_box(temp_addr)]])
        assert "verifier not found" in err.lower() or "assert" in err.lower()

        # Re-add — should work
        result = call(client, contract, "add_verifier", pk, addr,
                      args=[temp_addr], boxes=[[APP_ID, verifier_box(temp_addr)]])
        assert result.confirmed_round > 0

    def test_remove_verifier_not_admin_fails(self, client, contract, operator_acct, verifier_acct):
        """Non-admin cannot remove a verifier."""
        pk, addr = operator_acct
        v_addr = verifier_acct[1]
        err = call_expect_fail(
            client, contract, "remove_verifier",
            pk, addr,
            args=[v_addr],
            boxes=[[APP_ID, verifier_box(v_addr)]],
        )
        assert "only admin" in err.lower() or "assert" in err.lower()


# ── Section 2: Device Registration ───────────────────────────────────────────

class TestRegisterDevice:

    def test_register_device_happy(self, client, contract, operator_acct, verifier_acct):
        """Operator can register a new device."""
        pk, addr = operator_acct
        device_id = unique_device_id("R1")
        owner_hash = sha256("buyer_aadhaar_xyz")
        config_hash = sha256('{"ram":"8GB","ssd":"512GB"}')

        result = call(
            client, contract, "register_device",
            pk, addr,
            args=[device_id, owner_hash, config_hash],
            boxes=[
                [APP_ID, operator_box()],
                [APP_ID, device_box(device_id)],
            ],
        )
        assert result.confirmed_round > 0

        # Verify on-chain state
        state = get_device_state(client, contract, device_id)
        assert state["state"] == STATE_REGISTERED
        assert state["owner_hash"] == owner_hash
        assert state["config_hash"] == config_hash
        assert state["stamp_valid"] == False

    def test_register_device_double_fails(self, client, contract, operator_acct):
        """Registering the same device_id twice must fail."""
        pk, addr = operator_acct
        device_id = unique_device_id("R2")
        owner_hash = sha256("owner_a")
        config_hash = sha256("config_a")

        call(client, contract, "register_device", pk, addr,
             args=[device_id, owner_hash, config_hash],
             boxes=[[APP_ID, operator_box()], [APP_ID, device_box(device_id)]])

        err = call_expect_fail(client, contract, "register_device", pk, addr,
                               args=[device_id, owner_hash, config_hash],
                               boxes=[[APP_ID, operator_box()], [APP_ID, device_box(device_id)]])
        assert "device already registered" in err.lower() or "assert" in err.lower()

    def test_register_device_not_operator_fails(self, client, contract, verifier_acct):
        """Non-operator cannot register a device."""
        pk, addr = verifier_acct
        device_id = unique_device_id("R3")
        err = call_expect_fail(
            client, contract, "register_device",
            pk, addr,
            args=[device_id, sha256("x"), sha256("y")],
            boxes=[[APP_ID, operator_box()], [APP_ID, device_box(device_id)]],
        )
        assert "only operator" in err.lower() or "assert" in err.lower()


# ── Section 3: Verify Device ──────────────────────────────────────────────────

class TestVerifyDevice:

    def _register(self, client, contract, operator_acct, suffix=""):
        pk, addr = operator_acct
        device_id = unique_device_id(f"V{suffix}")
        call(client, contract, "register_device", pk, addr,
             args=[device_id, sha256("owner"), sha256("config")],
             boxes=[[APP_ID, operator_box()], [APP_ID, device_box(device_id)]])
        return device_id

    def test_verify_from_registered(self, client, contract, operator_acct, verifier_acct):
        """Verifier can stamp a REGISTERED device → state becomes VERIFIED."""
        device_id = self._register(client, contract, operator_acct, "A")
        pk, addr = verifier_acct
        config_hash = sha256("confirmed_config_A")

        result = call(
            client, contract, "verify_device",
            pk, addr,
            args=[device_id, config_hash],
            boxes=[
                [APP_ID, verifier_box(addr)],
                [APP_ID, device_box(device_id)],
            ],
        )
        assert result.confirmed_round > 0

        state = get_device_state(client, contract, device_id)
        assert state["state"] == STATE_VERIFIED
        assert state["stamp_valid"] == True
        assert state["config_hash"] == config_hash
        assert state["last_verifier"] == addr

    def test_verify_not_verifier_fails(self, client, contract, operator_acct):
        """Non-verifier cannot stamp a device."""
        device_id = self._register(client, contract, operator_acct, "B")
        pk, addr = operator_acct
        err = call_expect_fail(
            client, contract, "verify_device",
            pk, addr,
            args=[device_id, sha256("cfg")],
            boxes=[[APP_ID, verifier_box(addr)], [APP_ID, device_box(device_id)]],
        )
        assert "not an authorized verifier" in err.lower() or "assert" in err.lower()

    def test_verify_unregistered_device_fails(self, client, contract, verifier_acct):
        """Cannot verify a device that was never registered."""
        pk, addr = verifier_acct
        fake_id = "TL-DL-DOESNOTEXIST"
        err = call_expect_fail(
            client, contract, "verify_device",
            pk, addr,
            args=[fake_id, sha256("cfg")],
            boxes=[[APP_ID, verifier_box(addr)], [APP_ID, device_box(fake_id)]],
        )
        assert "device not registered" in err.lower() or "assert" in err.lower()

    def test_reverify_with_invalid_stamp(self, client, contract, operator_acct, verifier_acct):
        """
        Verifying an already-VERIFIED device with an invalid stamp should succeed
        (re-stamp after hardware change).
        """
        device_id = self._register(client, contract, operator_acct, "C")
        v_pk, v_addr = verifier_acct
        op_pk, op_addr = operator_acct

        # First verify
        call(client, contract, "verify_device", v_pk, v_addr,
             args=[device_id, sha256("cfg_original")],
             boxes=[[APP_ID, verifier_box(v_addr)], [APP_ID, device_box(device_id)]])

        # Declare hardware change → invalidates stamp
        call(client, contract, "declare_hardware_change", op_pk, op_addr,
             args=[device_id, sha256("cfg_new")],
             boxes=[[APP_ID, operator_box()], [APP_ID, device_box(device_id)]])

        state = get_device_state(client, contract, device_id)
        assert state["stamp_valid"] == False

        # Re-verify — should succeed
        result = call(client, contract, "verify_device", v_pk, v_addr,
                      args=[device_id, sha256("cfg_new")],
                      boxes=[[APP_ID, verifier_box(v_addr)], [APP_ID, device_box(device_id)]])
        assert result.confirmed_round > 0

        state = get_device_state(client, contract, device_id)
        assert state["stamp_valid"] == True

    def test_verify_already_verified_with_valid_stamp_fails(self, client, contract,
                                                             operator_acct, verifier_acct):
        """Cannot re-verify a VERIFIED device that still has a valid stamp."""
        device_id = self._register(client, contract, operator_acct, "D")
        v_pk, v_addr = verifier_acct

        call(client, contract, "verify_device", v_pk, v_addr,
             args=[device_id, sha256("cfg")],
             boxes=[[APP_ID, verifier_box(v_addr)], [APP_ID, device_box(device_id)]])

        err = call_expect_fail(client, contract, "verify_device", v_pk, v_addr,
                               args=[device_id, sha256("cfg2")],
                               boxes=[[APP_ID, verifier_box(v_addr)], [APP_ID, device_box(device_id)]])
        assert "verify only allowed" in err.lower() or "assert" in err.lower()


# ── Section 4: Transfer Device ────────────────────────────────────────────────

class TestTransferDevice:

    def _register_and_verify(self, client, contract, operator_acct, verifier_acct, suffix=""):
        op_pk, op_addr = operator_acct
        v_pk, v_addr = verifier_acct
        device_id = unique_device_id(f"T{suffix}")
        call(client, contract, "register_device", op_pk, op_addr,
             args=[device_id, sha256("owner"), sha256("cfg")],
             boxes=[[APP_ID, operator_box()], [APP_ID, device_box(device_id)]])
        call(client, contract, "verify_device", v_pk, v_addr,
             args=[device_id, sha256("cfg")],
             boxes=[[APP_ID, verifier_box(v_addr)], [APP_ID, device_box(device_id)]])
        return device_id

    def test_transfer_from_verified(self, client, contract, operator_acct, verifier_acct):
        """Verifier can transfer a VERIFIED device → state TRANSFERRED."""
        device_id = self._register_and_verify(client, contract, operator_acct, verifier_acct, "A")
        v_pk, v_addr = verifier_acct
        new_owner_hash = sha256("new_buyer_aadhaar")

        result = call(
            client, contract, "transfer_device",
            v_pk, v_addr,
            args=[device_id, new_owner_hash],
            boxes=[
                [APP_ID, verifier_box(v_addr)],
                [APP_ID, device_box(device_id)],
            ],
        )
        assert result.confirmed_round > 0

        state = get_device_state(client, contract, device_id)
        assert state["state"] == STATE_TRANSFERRED
        assert state["owner_hash"] == new_owner_hash
        # stamp_valid is NOT reset by transfer_device (only by hardware change / dispute)

    def test_transfer_from_registered_fails(self, client, contract, operator_acct, verifier_acct):
        """Cannot transfer a device that is only REGISTERED (not yet VERIFIED)."""
        op_pk, op_addr = operator_acct
        device_id = unique_device_id("TB")
        call(client, contract, "register_device", op_pk, op_addr,
             args=[device_id, sha256("o"), sha256("c")],
             boxes=[[APP_ID, operator_box()], [APP_ID, device_box(device_id)]])

        v_pk, v_addr = verifier_acct
        err = call_expect_fail(client, contract, "transfer_device", v_pk, v_addr,
                               args=[device_id, sha256("new_o")],
                               boxes=[[APP_ID, verifier_box(v_addr)], [APP_ID, device_box(device_id)]])
        assert "transfer only allowed from verified" in err.lower() or "assert" in err.lower()

    def test_transfer_with_invalid_stamp_fails(self, client, contract,
                                               operator_acct, verifier_acct):
        """Cannot transfer after hardware change (stamp invalid)."""
        device_id = self._register_and_verify(client, contract, operator_acct, verifier_acct, "C")
        op_pk, op_addr = operator_acct
        v_pk, v_addr = verifier_acct

        # Declare hardware change → invalidates stamp
        call(client, contract, "declare_hardware_change", op_pk, op_addr,
             args=[device_id, sha256("new_cfg")],
             boxes=[[APP_ID, operator_box()], [APP_ID, device_box(device_id)]])

        err = call_expect_fail(client, contract, "transfer_device", v_pk, v_addr,
                               args=[device_id, sha256("new_o")],
                               boxes=[[APP_ID, verifier_box(v_addr)], [APP_ID, device_box(device_id)]])
        assert "stamp not valid" in err.lower() or "assert" in err.lower()

    def test_transfer_not_verifier_fails(self, client, contract, operator_acct, verifier_acct):
        """Non-verifier cannot execute a transfer."""
        device_id = self._register_and_verify(client, contract, operator_acct, verifier_acct, "D")
        op_pk, op_addr = operator_acct
        err = call_expect_fail(client, contract, "transfer_device", op_pk, op_addr,
                               args=[device_id, sha256("x")],
                               boxes=[[APP_ID, verifier_box(op_addr)], [APP_ID, device_box(device_id)]])
        assert "not an authorized verifier" in err.lower() or "assert" in err.lower()


# ── Section 5: Declare Hardware Change ───────────────────────────────────────

class TestDeclareHardwareChange:

    def _register(self, client, contract, operator_acct, suffix=""):
        pk, addr = operator_acct
        device_id = unique_device_id(f"H{suffix}")
        call(client, contract, "register_device", pk, addr,
             args=[device_id, sha256("o"), sha256("c")],
             boxes=[[APP_ID, operator_box()], [APP_ID, device_box(device_id)]])
        return device_id

    def test_declare_hardware_change_from_registered(self, client, contract, operator_acct):
        """Operator can declare hardware change on a REGISTERED device."""
        device_id = self._register(client, contract, operator_acct, "A")
        pk, addr = operator_acct
        new_hash = sha256("cfg_after_ram_upgrade")

        result = call(
            client, contract, "declare_hardware_change",
            pk, addr,
            args=[device_id, new_hash],
            boxes=[[APP_ID, operator_box()], [APP_ID, device_box(device_id)]],
        )
        assert result.confirmed_round > 0

        state = get_device_state(client, contract, device_id)
        assert state["config_hash"] == new_hash
        assert state["stamp_valid"] == False

    def test_declare_hardware_change_from_verified(self, client, contract,
                                                   operator_acct, verifier_acct):
        """Operator can declare hardware change on a VERIFIED device → stamp invalidated."""
        device_id = self._register(client, contract, operator_acct, "B")
        op_pk, op_addr = operator_acct
        v_pk, v_addr = verifier_acct

        call(client, contract, "verify_device", v_pk, v_addr,
             args=[device_id, sha256("c")],
             boxes=[[APP_ID, verifier_box(v_addr)], [APP_ID, device_box(device_id)]])

        state = get_device_state(client, contract, device_id)
        assert state["stamp_valid"] == True

        call(client, contract, "declare_hardware_change", op_pk, op_addr,
             args=[device_id, sha256("new_cfg")],
             boxes=[[APP_ID, operator_box()], [APP_ID, device_box(device_id)]])

        state = get_device_state(client, contract, device_id)
        assert state["stamp_valid"] == False

    def test_declare_hardware_change_while_disputed_fails(self, client, contract,
                                                          operator_acct, verifier_acct):
        """Cannot declare hardware change on a DISPUTED device."""
        device_id = self._register(client, contract, operator_acct, "C")
        op_pk, op_addr = operator_acct
        v_pk, v_addr = verifier_acct

        # Raise dispute
        call(client, contract, "raise_dispute", op_pk, op_addr,
             args=[device_id, int(DISPUTE_TYPE_MISREPRESENTED)],
             boxes=[
                 [APP_ID, operator_box()],
                 [APP_ID, verifier_box(op_addr)],
                 [APP_ID, device_box(device_id)],
             ])

        err = call_expect_fail(client, contract, "declare_hardware_change", op_pk, op_addr,
                               args=[device_id, sha256("cfg")],
                               boxes=[[APP_ID, operator_box()], [APP_ID, device_box(device_id)]])
        assert "cannot declare hardware change while disputed" in err.lower() or "assert" in err.lower()

    def test_declare_hardware_change_not_operator_fails(self, client, contract,
                                                        operator_acct, verifier_acct):
        """Non-operator cannot declare a hardware change."""
        device_id = self._register(client, contract, operator_acct, "D")
        v_pk, v_addr = verifier_acct
        err = call_expect_fail(client, contract, "declare_hardware_change", v_pk, v_addr,
                               args=[device_id, sha256("cfg")],
                               boxes=[[APP_ID, operator_box()], [APP_ID, device_box(device_id)]])
        assert "only operator" in err.lower() or "assert" in err.lower()

    def test_declare_hardware_change_on_terminal_fails(self, client, contract,
                                                       operator_acct, verifier_acct,
                                                       admin_acct):
        """Cannot declare hardware change on a RECYCLED device."""
        op_pk, op_addr = operator_acct
        v_pk, v_addr = verifier_acct
        adm_pk, adm_addr = admin_acct
        r_pk, r_addr = None, None  # populated below

        device_id = unique_device_id("HE")
        call(client, contract, "register_device", op_pk, op_addr,
             args=[device_id, sha256("o"), sha256("c")],
             boxes=[[APP_ID, operator_box()], [APP_ID, device_box(device_id)]])
        call(client, contract, "verify_device", v_pk, v_addr,
             args=[device_id, sha256("c")],
             boxes=[[APP_ID, verifier_box(v_addr)], [APP_ID, device_box(device_id)]])

        # mark_exported (admin) → EXPORTED terminal
        call(client, contract, "mark_exported", adm_pk, adm_addr,
             args=[device_id],
             boxes=[[APP_ID, device_box(device_id)]])

        err = call_expect_fail(client, contract, "declare_hardware_change", op_pk, op_addr,
                               args=[device_id, sha256("cfg")],
                               boxes=[[APP_ID, operator_box()], [APP_ID, device_box(device_id)]])
        assert "exported" in err.lower() or "terminal" in err.lower() or "assert" in err.lower()


# ── Section 6: Raise & Resolve Dispute ───────────────────────────────────────

class TestDispute:

    def _register(self, client, contract, operator_acct, suffix=""):
        pk, addr = operator_acct
        device_id = unique_device_id(f"D{suffix}")
        call(client, contract, "register_device", pk, addr,
             args=[device_id, sha256("o"), sha256("c")],
             boxes=[[APP_ID, operator_box()], [APP_ID, device_box(device_id)]])
        return device_id

    def test_raise_dispute_by_operator(self, client, contract, operator_acct):
        """Operator can raise a dispute on a REGISTERED device."""
        device_id = self._register(client, contract, operator_acct, "A")
        pk, addr = operator_acct

        result = call(
            client, contract, "raise_dispute",
            pk, addr,
            args=[device_id, int(DISPUTE_TYPE_MISREPRESENTED)],
            boxes=[
                [APP_ID, operator_box()],
                [APP_ID, verifier_box(addr)],   # contract checks both maps
                [APP_ID, device_box(device_id)],
            ],
        )
        assert result.confirmed_round > 0

        state = get_device_state(client, contract, device_id)
        assert state["state"] == STATE_DISPUTED
        assert state["stamp_valid"] == False

    def test_raise_dispute_by_verifier(self, client, contract, operator_acct, verifier_acct):
        """Verifier can also raise a dispute."""
        device_id = self._register(client, contract, operator_acct, "B")
        v_pk, v_addr = verifier_acct
        op_pk, op_addr = operator_acct

        result = call(
            client, contract, "raise_dispute",
            v_pk, v_addr,
            args=[device_id, int(DISPUTE_TYPE_MISREPRESENTED)],
            boxes=[
                [APP_ID, operator_box()],
                [APP_ID, verifier_box(v_addr)],
                [APP_ID, device_box(device_id)],
            ],
        )
        assert result.confirmed_round > 0
        state = get_device_state(client, contract, device_id)
        assert state["state"] == STATE_DISPUTED

    def test_raise_dispute_by_unauthorized_fails(self, client, contract,
                                                 operator_acct, buyer_acct, admin_acct):
        """buyer must not be a verifier or operator."""
        # Ensure buyer is not a verifier (defensive cleanup)
        adm_pk, adm_addr = admin_acct
        b_pk, b_addr = buyer_acct
        try:
            call(client, contract, "remove_verifier", adm_pk, adm_addr,
                 args=[b_addr], boxes=[[APP_ID, verifier_box(b_addr)]])
        except Exception:
            pass  # already removed, that's fine

        device_id = self._register(client, contract, operator_acct, "C")
        err = call_expect_fail(
            client, contract, "raise_dispute",
            b_pk, b_addr,
            args=[device_id, int(DISPUTE_TYPE_MISREPRESENTED)],
            boxes=[[APP_ID, operator_box()], [APP_ID, verifier_box(b_addr)],
                   [APP_ID, device_box(device_id)]],
        )
        assert "not authorized to raise dispute" in err.lower() or "assert" in err.lower()

    def test_resolve_dispute_to_registered(self, client, contract,
                                           operator_acct, admin_acct):
        """Admin can resolve a DISPUTED device back to REGISTERED."""
        device_id = self._register(client, contract, operator_acct, "D")
        op_pk, op_addr = operator_acct
        adm_pk, adm_addr = admin_acct

        call(client, contract, "raise_dispute", op_pk, op_addr,
             args=[device_id, int(DISPUTE_TYPE_MISREPRESENTED)],
             boxes=[
                 [APP_ID, operator_box()],
                 [APP_ID, verifier_box(op_addr)],
                 [APP_ID, device_box(device_id)],
             ])

        result = call(client, contract, "resolve_dispute", adm_pk, adm_addr,
                      args=[device_id, int(STATE_REGISTERED)],
                      boxes=[[APP_ID, device_box(device_id)]])
        assert result.confirmed_round > 0

        state = get_device_state(client, contract, device_id)
        assert state["state"] == STATE_REGISTERED
        assert state["stamp_valid"] == False   # always cleared on resolve

    def test_resolve_dispute_to_verified(self, client, contract,
                                         operator_acct, verifier_acct, admin_acct):
        """Admin can resolve a dispute into VERIFIED."""
        device_id = self._register(client, contract, operator_acct, "E")
        op_pk, op_addr = operator_acct
        adm_pk, adm_addr = admin_acct

        call(client, contract, "raise_dispute", op_pk, op_addr,
             args=[device_id, int(DISPUTE_TYPE_MISREPRESENTED)],
             boxes=[
                 [APP_ID, operator_box()],
                 [APP_ID, verifier_box(op_addr)],
                 [APP_ID, device_box(device_id)],
             ])

        result = call(client, contract, "resolve_dispute", adm_pk, adm_addr,
                      args=[device_id, int(STATE_VERIFIED)],
                      boxes=[[APP_ID, device_box(device_id)]])
        assert result.confirmed_round > 0
        state = get_device_state(client, contract, device_id)
        assert state["state"] == STATE_VERIFIED

    def test_resolve_dispute_to_transferred(self, client, contract,
                                             operator_acct, verifier_acct, admin_acct):
        """Admin can resolve a dispute into TRANSFERRED."""
        op_pk, op_addr = operator_acct
        v_pk, v_addr = verifier_acct
        adm_pk, adm_addr = admin_acct
        device_id = unique_device_id("DT")

        call(client, contract, "register_device", op_pk, op_addr,
             args=[device_id, sha256("o"), sha256("c")],
             boxes=[[APP_ID, operator_box()], [APP_ID, device_box(device_id)]])
        call(client, contract, "verify_device", v_pk, v_addr,
             args=[device_id, sha256("c")],
             boxes=[[APP_ID, verifier_box(v_addr)], [APP_ID, device_box(device_id)]])
        call(client, contract, "transfer_device", v_pk, v_addr,
             args=[device_id, sha256("new_o")],
             boxes=[[APP_ID, verifier_box(v_addr)], [APP_ID, device_box(device_id)]])

        call(client, contract, "raise_dispute", op_pk, op_addr,
             args=[device_id, int(DISPUTE_TYPE_MISREPRESENTED)],
             boxes=[
                 [APP_ID, operator_box()],
                 [APP_ID, verifier_box(op_addr)],
                 [APP_ID, device_box(device_id)],
             ])

        result = call(client, contract, "resolve_dispute", adm_pk, adm_addr,
                      args=[device_id, int(STATE_TRANSFERRED)],
                      boxes=[[APP_ID, device_box(device_id)]])
        assert result.confirmed_round > 0
        state = get_device_state(client, contract, device_id)
        assert state["state"] == STATE_TRANSFERRED

    def test_resolve_dispute_to_terminal_fails(self, client, contract,
                                               operator_acct, admin_acct):
        """resolve_dispute must not accept terminal states (RECYCLED, EXPORTED, DISPUTED)."""
        device_id = self._register(client, contract, operator_acct, "F")
        op_pk, op_addr = operator_acct
        adm_pk, adm_addr = admin_acct

        call(client, contract, "raise_dispute", op_pk, op_addr,
             args=[device_id, int(DISPUTE_TYPE_MISREPRESENTED)],
             boxes=[
                 [APP_ID, operator_box()],
                 [APP_ID, verifier_box(op_addr)],
                 [APP_ID, device_box(device_id)],
             ])

        for bad_state in [STATE_DISPUTED, STATE_RECYCLED, STATE_EXPORTED]:
            err = call_expect_fail(client, contract, "resolve_dispute", adm_pk, adm_addr,
                                   args=[device_id, int(bad_state)],
                                   boxes=[[APP_ID, device_box(device_id)]])
            assert "must resolve into" in err.lower() or "assert" in err.lower()

    def test_resolve_non_disputed_device_fails(self, client, contract,
                                               operator_acct, admin_acct):
        """Cannot resolve_dispute on a device that is not disputed."""
        device_id = self._register(client, contract, operator_acct, "G")
        adm_pk, adm_addr = admin_acct
        err = call_expect_fail(client, contract, "resolve_dispute", adm_pk, adm_addr,
                               args=[device_id, int(STATE_REGISTERED)],
                               boxes=[[APP_ID, device_box(device_id)]])
        assert "device not disputed" in err.lower() or "assert" in err.lower()

    def test_resolve_dispute_not_admin_fails(self, client, contract,
                                             operator_acct, verifier_acct):
        """Non-admin cannot resolve a dispute."""
        device_id = self._register(client, contract, operator_acct, "H")
        op_pk, op_addr = operator_acct
        v_pk, v_addr = verifier_acct

        call(client, contract, "raise_dispute", op_pk, op_addr,
             args=[device_id, int(DISPUTE_TYPE_MISREPRESENTED)],
             boxes=[
                 [APP_ID, operator_box()],
                 [APP_ID, verifier_box(op_addr)],
                 [APP_ID, device_box(device_id)],
             ])

        err = call_expect_fail(client, contract, "resolve_dispute", v_pk, v_addr,
                               args=[device_id, int(STATE_REGISTERED)],
                               boxes=[[APP_ID, device_box(device_id)]])
        assert "only admin" in err.lower() or "assert" in err.lower()


# ── Section 7: Mark Recycled ──────────────────────────────────────────────────

class TestMarkRecycled:

    def _setup_transferred(self, client, contract, operator_acct, verifier_acct,
                           recycler_acct, suffix=""):
        op_pk, op_addr = operator_acct
        v_pk, v_addr = verifier_acct
        r_addr = recycler_acct[1]
        device_id = unique_device_id(f"RC{suffix}")

        call(client, contract, "register_device", op_pk, op_addr,
             args=[device_id, sha256("o"), sha256("c")],
             boxes=[[APP_ID, operator_box()], [APP_ID, device_box(device_id)]])
        call(client, contract, "verify_device", v_pk, v_addr,
             args=[device_id, sha256("c")],
             boxes=[[APP_ID, verifier_box(v_addr)], [APP_ID, device_box(device_id)]])
        # Transfer to recycler
        call(client, contract, "transfer_device", v_pk, v_addr,
             args=[device_id, sha256(r_addr)],
             boxes=[[APP_ID, verifier_box(v_addr)], [APP_ID, device_box(device_id)]])
        return device_id

    def test_mark_recycled_happy(self, client, contract, operator_acct,
                                  verifier_acct, recycler_acct):
        """Recycler can mark a TRANSFERRED device as RECYCLED."""
        device_id = self._setup_transferred(client, contract, operator_acct,
                                            verifier_acct, recycler_acct, "A")
        r_pk, r_addr = recycler_acct

        result = call(
            client, contract, "mark_recycled",
            r_pk, r_addr,
            args=[device_id],
            boxes=[
                [APP_ID, recycler_box(r_addr)],
                [APP_ID, device_box(device_id)],
            ],
        )
        assert result.confirmed_round > 0

        state = get_device_state(client, contract, device_id)
        assert state["state"] == STATE_RECYCLED

    def test_mark_recycled_from_registered_fails(self, client, contract,
                                                  operator_acct, recycler_acct):
        """Cannot recycle a device that is only REGISTERED (must be TRANSFERRED first)."""
        op_pk, op_addr = operator_acct
        r_pk, r_addr = recycler_acct
        device_id = unique_device_id("RCB")
        call(client, contract, "register_device", op_pk, op_addr,
             args=[device_id, sha256("o"), sha256("c")],
             boxes=[[APP_ID, operator_box()], [APP_ID, device_box(device_id)]])

        err = call_expect_fail(client, contract, "mark_recycled", r_pk, r_addr,
                               args=[device_id],
                               boxes=[[APP_ID, recycler_box(r_addr)], [APP_ID, device_box(device_id)]])
        assert "freshly transferred" in err.lower() or "assert" in err.lower()

    def test_mark_recycled_not_recycler_fails(self, client, contract,
                                               operator_acct, verifier_acct, recycler_acct):
        """Non-recycler cannot mark a device as RECYCLED."""
        device_id = self._setup_transferred(client, contract, operator_acct,
                                            verifier_acct, recycler_acct, "C")
        op_pk, op_addr = operator_acct
        err = call_expect_fail(client, contract, "mark_recycled", op_pk, op_addr,
                               args=[device_id],
                               boxes=[[APP_ID, recycler_box(op_addr)], [APP_ID, device_box(device_id)]])
        assert "not an authorized recycler" in err.lower() or "assert" in err.lower()

    def test_mark_recycled_on_recycled_fails(self, client, contract,
                                              operator_acct, verifier_acct, recycler_acct):
        """Cannot recycle an already RECYCLED device (terminal state)."""
        device_id = self._setup_transferred(client, contract, operator_acct,
                                            verifier_acct, recycler_acct, "D")
        r_pk, r_addr = recycler_acct
        call(client, contract, "mark_recycled", r_pk, r_addr,
             args=[device_id],
             boxes=[[APP_ID, recycler_box(r_addr)], [APP_ID, device_box(device_id)]])

        err = call_expect_fail(client, contract, "mark_recycled", r_pk, r_addr,
                               args=[device_id],
                               boxes=[[APP_ID, recycler_box(r_addr)], [APP_ID, device_box(device_id)]])
        assert "recycled" in err.lower() or "terminal" in err.lower() or "assert" in err.lower()


# ── Section 8: Mark Exported ──────────────────────────────────────────────────

class TestMarkExported:

    def test_mark_exported_from_registered(self, client, contract,
                                            operator_acct, admin_acct):
        """Admin can mark a REGISTERED device as EXPORTED (terminal)."""
        op_pk, op_addr = operator_acct
        adm_pk, adm_addr = admin_acct
        device_id = unique_device_id("EX1")

        call(client, contract, "register_device", op_pk, op_addr,
             args=[device_id, sha256("o"), sha256("c")],
             boxes=[[APP_ID, operator_box()], [APP_ID, device_box(device_id)]])

        result = call(client, contract, "mark_exported", adm_pk, adm_addr,
                      args=[device_id], boxes=[[APP_ID, device_box(device_id)]])
        assert result.confirmed_round > 0

        state = get_device_state(client, contract, device_id)
        assert state["state"] == STATE_EXPORTED

    def test_mark_exported_not_admin_fails(self, client, contract,
                                            operator_acct, verifier_acct):
        """Non-admin cannot mark a device as EXPORTED."""
        op_pk, op_addr = operator_acct
        device_id = unique_device_id("EX2")
        call(client, contract, "register_device", op_pk, op_addr,
             args=[device_id, sha256("o"), sha256("c")],
             boxes=[[APP_ID, operator_box()], [APP_ID, device_box(device_id)]])

        v_pk, v_addr = verifier_acct
        err = call_expect_fail(client, contract, "mark_exported", v_pk, v_addr,
                               args=[device_id], boxes=[[APP_ID, device_box(device_id)]])
        assert "only admin" in err.lower() or "assert" in err.lower()

    def test_mark_exported_on_exported_fails(self, client, contract,
                                              operator_acct, admin_acct):
        """Cannot export an already EXPORTED device."""
        op_pk, op_addr = operator_acct
        adm_pk, adm_addr = admin_acct
        device_id = unique_device_id("EX3")
        call(client, contract, "register_device", op_pk, op_addr,
             args=[device_id, sha256("o"), sha256("c")],
             boxes=[[APP_ID, operator_box()], [APP_ID, device_box(device_id)]])
        call(client, contract, "mark_exported", adm_pk, adm_addr,
             args=[device_id], boxes=[[APP_ID, device_box(device_id)]])

        err = call_expect_fail(client, contract, "mark_exported", adm_pk, adm_addr,
                               args=[device_id], boxes=[[APP_ID, device_box(device_id)]])
        assert "exported" in err.lower() or "terminal" in err.lower() or "assert" in err.lower()


# ── Section 9: Read-only Queries ──────────────────────────────────────────────

class TestReadOnly:

    def test_get_device_state_unregistered_fails(self, client, contract):
        """get_device_state on a non-existent device must fail."""
        fake = "TL-DL-FAKEFAKE"
        pk, addr = _load_account("ADMIN")
        
        atc = AtomicTransactionComposer()
        method = contract.get_method_by_name("get_device_state")
        atc.add_method_call(
            app_id=APP_ID, method=method, sender=addr,
            sp=sp(client), signer=AccountTransactionSigner(pk),
            method_args=[fake],
            boxes=[[APP_ID, device_box(fake)]],
        )
        try:
            atc.execute(client, 4)
            raise AssertionError("Should have failed on unknown device")
        except Exception as e:
            assert "device not registered" in str(e).lower() or "assert" in str(e).lower()

    def test_can_verify_true_for_registered(self, client, contract, operator_acct):
        """can_verify returns True for a REGISTERED device."""
        op_pk, op_addr = operator_acct
        device_id = unique_device_id("QV1")
        call(client, contract, "register_device", op_pk, op_addr,
             args=[device_id, sha256("o"), sha256("c")],
             boxes=[[APP_ID, operator_box()], [APP_ID, device_box(device_id)]])
        assert call_bool_readonly(client, contract, "can_verify", device_id) == True

    def test_can_verify_false_for_verified_with_valid_stamp(self, client, contract,
                                                             operator_acct, verifier_acct):
        """can_verify returns False for a VERIFIED device with a valid stamp."""
        op_pk, op_addr = operator_acct
        v_pk, v_addr = verifier_acct
        device_id = unique_device_id("QV2")
        call(client, contract, "register_device", op_pk, op_addr,
             args=[device_id, sha256("o"), sha256("c")],
             boxes=[[APP_ID, operator_box()], [APP_ID, device_box(device_id)]])
        call(client, contract, "verify_device", v_pk, v_addr,
             args=[device_id, sha256("c")],
             boxes=[[APP_ID, verifier_box(v_addr)], [APP_ID, device_box(device_id)]])
        assert call_bool_readonly(client, contract, "can_verify", device_id) == False

    def test_can_verify_false_for_unregistered(self, client, contract):
        """can_verify returns False for a device not in the map."""
        assert call_bool_readonly(client, contract, "can_verify", "TL-DL-NOPE999") == False

    def test_can_transfer_true(self, client, contract, operator_acct, verifier_acct):
        """can_transfer is True for VERIFIED + valid stamp."""
        op_pk, op_addr = operator_acct
        v_pk, v_addr = verifier_acct
        device_id = unique_device_id("QT1")
        call(client, contract, "register_device", op_pk, op_addr,
             args=[device_id, sha256("o"), sha256("c")],
             boxes=[[APP_ID, operator_box()], [APP_ID, device_box(device_id)]])
        call(client, contract, "verify_device", v_pk, v_addr,
             args=[device_id, sha256("c")],
             boxes=[[APP_ID, verifier_box(v_addr)], [APP_ID, device_box(device_id)]])
        assert call_bool_readonly(client, contract, "can_transfer", device_id) == True

    def test_can_transfer_false_no_stamp(self, client, contract, operator_acct,
                                          verifier_acct):
        """can_transfer is False after hardware change invalidates the stamp."""
        op_pk, op_addr = operator_acct
        v_pk, v_addr = verifier_acct
        device_id = unique_device_id("QT2")
        call(client, contract, "register_device", op_pk, op_addr,
             args=[device_id, sha256("o"), sha256("c")],
             boxes=[[APP_ID, operator_box()], [APP_ID, device_box(device_id)]])
        call(client, contract, "verify_device", v_pk, v_addr,
             args=[device_id, sha256("c")],
             boxes=[[APP_ID, verifier_box(v_addr)], [APP_ID, device_box(device_id)]])
        call(client, contract, "declare_hardware_change", op_pk, op_addr,
             args=[device_id, sha256("new_c")],
             boxes=[[APP_ID, operator_box()], [APP_ID, device_box(device_id)]])
        assert call_bool_readonly(client, contract, "can_transfer", device_id) == False

    def test_can_recycle_true(self, client, contract, operator_acct, verifier_acct):
        """can_recycle is True for a TRANSFERRED device."""
        op_pk, op_addr = operator_acct
        v_pk, v_addr = verifier_acct
        device_id = unique_device_id("QR1")
        call(client, contract, "register_device", op_pk, op_addr,
             args=[device_id, sha256("o"), sha256("c")],
             boxes=[[APP_ID, operator_box()], [APP_ID, device_box(device_id)]])
        call(client, contract, "verify_device", v_pk, v_addr,
             args=[device_id, sha256("c")],
             boxes=[[APP_ID, verifier_box(v_addr)], [APP_ID, device_box(device_id)]])
        call(client, contract, "transfer_device", v_pk, v_addr,
             args=[device_id, sha256("new_o")],
             boxes=[[APP_ID, verifier_box(v_addr)], [APP_ID, device_box(device_id)]])
        assert call_bool_readonly(client, contract, "can_recycle", device_id) == True

    def test_can_recycle_false_for_registered(self, client, contract, operator_acct):
        """can_recycle is False for a REGISTERED device."""
        op_pk, op_addr = operator_acct
        device_id = unique_device_id("QR2")
        call(client, contract, "register_device", op_pk, op_addr,
             args=[device_id, sha256("o"), sha256("c")],
             boxes=[[APP_ID, operator_box()], [APP_ID, device_box(device_id)]])
        assert call_bool_readonly(client, contract, "can_recycle", device_id) == False


# ── Section 10: Full Lifecycle Integration Test ───────────────────────────────

class TestFullLifecycle:

    def test_full_lifecycle_register_to_recycle(self, client, contract,
                                                 operator_acct, verifier_acct,
                                                 recycler_acct, admin_acct):
        """
        Full happy-path lifecycle:
        REGISTERED → VERIFIED → TRANSFERRED → VERIFIED (re-stamp)
        → TRANSFERRED → RECYCLED
        """
        op_pk, op_addr = operator_acct
        v_pk, v_addr = verifier_acct
        r_pk, r_addr = recycler_acct
        adm_pk, adm_addr = admin_acct

        device_id = unique_device_id("FULL")
        owner_hash_1 = sha256("first_buyer")
        owner_hash_2 = sha256("second_buyer")
        config_hash  = sha256("16GB RAM 1TB SSD i7")

        # 1. Register
        call(client, contract, "register_device", op_pk, op_addr,
             args=[device_id, owner_hash_1, config_hash],
             boxes=[[APP_ID, operator_box()], [APP_ID, device_box(device_id)]])
        s = get_device_state(client, contract, device_id)
        assert s["state"] == STATE_REGISTERED

        # 2. First verify
        call(client, contract, "verify_device", v_pk, v_addr,
             args=[device_id, config_hash],
             boxes=[[APP_ID, verifier_box(v_addr)], [APP_ID, device_box(device_id)]])
        s = get_device_state(client, contract, device_id)
        assert s["state"] == STATE_VERIFIED
        assert s["stamp_valid"] == True

        # 3. Transfer to second buyer
        call(client, contract, "transfer_device", v_pk, v_addr,
             args=[device_id, owner_hash_2],
             boxes=[[APP_ID, verifier_box(v_addr)], [APP_ID, device_box(device_id)]])
        s = get_device_state(client, contract, device_id)
        assert s["state"] == STATE_TRANSFERRED
        assert s["owner_hash"] == owner_hash_2

        # 4. Re-verify after transfer
        call(client, contract, "verify_device", v_pk, v_addr,
             args=[device_id, config_hash],
             boxes=[[APP_ID, verifier_box(v_addr)], [APP_ID, device_box(device_id)]])
        s = get_device_state(client, contract, device_id)
        assert s["state"] == STATE_VERIFIED

        # 5. Transfer to recycler
        call(client, contract, "transfer_device", v_pk, v_addr,
             args=[device_id, sha256(r_addr)],
             boxes=[[APP_ID, verifier_box(v_addr)], [APP_ID, device_box(device_id)]])
        s = get_device_state(client, contract, device_id)
        assert s["state"] == STATE_TRANSFERRED

        # 6. Recycle
        call(client, contract, "mark_recycled", r_pk, r_addr,
             args=[device_id],
             boxes=[[APP_ID, recycler_box(r_addr)], [APP_ID, device_box(device_id)]])
        s = get_device_state(client, contract, device_id)
        assert s["state"] == STATE_RECYCLED

        # 7. Any further action should fail (terminal)
        err = call_expect_fail(client, contract, "verify_device", v_pk, v_addr,
                               args=[device_id, config_hash],
                               boxes=[[APP_ID, verifier_box(v_addr)], [APP_ID, device_box(device_id)]])
        assert "recycled" in err.lower() or "terminal" in err.lower() or "assert" in err.lower()

    def test_full_lifecycle_with_dispute_and_resolution(self, client, contract,
                                                         operator_acct, verifier_acct,
                                                         admin_acct):
        """
        Lifecycle with a dispute in the middle:
        REGISTERED → VERIFIED → DISPUTED → REGISTERED (resolved) → VERIFIED → TRANSFERRED
        """
        op_pk, op_addr = operator_acct
        v_pk, v_addr = verifier_acct
        adm_pk, adm_addr = admin_acct

        device_id = unique_device_id("DISP")
        config_hash = sha256("original_config")

        call(client, contract, "register_device", op_pk, op_addr,
             args=[device_id, sha256("owner"), config_hash],
             boxes=[[APP_ID, operator_box()], [APP_ID, device_box(device_id)]])

        call(client, contract, "verify_device", v_pk, v_addr,
             args=[device_id, config_hash],
             boxes=[[APP_ID, verifier_box(v_addr)], [APP_ID, device_box(device_id)]])

        call(client, contract, "raise_dispute", op_pk, op_addr,
             args=[device_id, int(DISPUTE_TYPE_MISREPRESENTED)],
             boxes=[
                 [APP_ID, operator_box()],
                 [APP_ID, verifier_box(op_addr)],
                 [APP_ID, device_box(device_id)],
             ])
        s = get_device_state(client, contract, device_id)
        assert s["state"] == STATE_DISPUTED

        call(client, contract, "resolve_dispute", adm_pk, adm_addr,
             args=[device_id, int(STATE_REGISTERED)],
             boxes=[[APP_ID, device_box(device_id)]])
        s = get_device_state(client, contract, device_id)
        assert s["state"] == STATE_REGISTERED
        assert s["stamp_valid"] == False

        # Must re-verify after dispute resolution
        call(client, contract, "verify_device", v_pk, v_addr,
             args=[device_id, config_hash],
             boxes=[[APP_ID, verifier_box(v_addr)], [APP_ID, device_box(device_id)]])
        s = get_device_state(client, contract, device_id)
        assert s["state"] == STATE_VERIFIED
        assert s["stamp_valid"] == True

        call(client, contract, "transfer_device", v_pk, v_addr,
             args=[device_id, sha256("new_owner")],
             boxes=[[APP_ID, verifier_box(v_addr)], [APP_ID, device_box(device_id)]])
        s = get_device_state(client, contract, device_id)
        assert s["state"] == STATE_TRANSFERRED