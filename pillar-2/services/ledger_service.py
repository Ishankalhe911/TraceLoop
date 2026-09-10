import os
import json
from typing import Dict, Any, List, Optional
from algosdk.v2client import algod
from algosdk import mnemonic, account
from algosdk.abi import Contract
from algosdk.atomic_transaction_composer import (
    AtomicTransactionComposer,
    AccountTransactionSigner,
    TransactionWithSigner
)
from algosdk.v2client.models import SimulateRequest

# --- CONFIGURATION & ENV VARS ---
# Uses App ID 770956001 as defined in the final environment specs[cite: 3]
APP_ID = int(os.getenv("APP_ID", "770956001"))
ALGOD_URL = os.getenv("ALGOD_URL", "https://testnet-api.algonode.cloud")
ALGOD_TOKEN = os.getenv("ALGOD_TOKEN", "")

# Load Role Mnemonics for transaction signing[cite: 3]
OPERATOR_MNEMONIC = os.getenv("OPERATOR_MNEMONIC", "")
VERIFIER_MNEMONIC = os.getenv("VERIFIER_MNEMONIC", "")
RECYCLER_MNEMONIC = os.getenv("RECYCLER_MNEMONIC", "")
ADMIN_MNEMONIC = os.getenv("ADMIN_MNEMONIC", "")

# --- INITIALIZATION ---
client = algod.AlgodClient(ALGOD_TOKEN, ALGOD_URL)

# Locate ABI. Assuming the FastAPI app is run from pillar-2/ and artifacts are in pillar-1/
# Adjust this path if your working directory differs.
ABI_PATH = os.getenv(
    "ABI_PATH", 
    os.path.join(os.path.dirname(__file__), "../../pillar-1/smart_contract/artifacts/contract.json")
)

with open(ABI_PATH, "r") as f:
    contract = Contract.from_json(f.read())


class LedgerService:
    """
    Core service connecting Web2 FastAPI routes to the Algorand TestNet Smart Contract.
    All state-changing methods are strictly synchronous[cite: 3].
    """

    @staticmethod
    def _get_signer_and_address(role: str) -> tuple[AccountTransactionSigner, str]:
        """Returns the appropriate signer and address based on the required role[cite: 3]."""
        mn = ""
        if role.upper() == "OPERATOR":
            mn = OPERATOR_MNEMONIC
        elif role.upper() == "VERIFIER":
            mn = VERIFIER_MNEMONIC
        elif role.upper() == "RECYCLER":
            mn = RECYCLER_MNEMONIC
        elif role.upper() == "ADMIN":
            mn = ADMIN_MNEMONIC
        else:
            raise ValueError(f"Invalid role requested for signing: {role}")
        
        if not mn:
            raise ValueError(f"Mnemonic for role {role} is not configured in .env")

        pk = mnemonic.to_private_key(mn)
        addr = account.address_from_private_key(pk)
        return AccountTransactionSigner(pk), addr

    # --- BOX REFERENCE GENERATORS ---
    
    @staticmethod
    def get_operator_box() -> List[Any]:
        return [APP_ID, b"operator"]

    @staticmethod
    def get_verifier_box(addr: str) -> List[Any]:
        return [APP_ID, b"verifier_" + account.decode_address(addr)]

    @staticmethod
    def get_recycler_box(addr: str) -> List[Any]:
        return [APP_ID, b"recycler_" + account.decode_address(addr)]

    @staticmethod
    def get_device_box(device_id: str) -> List[Any]:
        """
        Calculates the ARC-4 dynamic string box key.
        Format: b"device_" + uint16(len) + bytes[cite: 3, 4]
        """
        device_bytes = device_id.encode("utf-8")
        length_prefix = len(device_bytes).to_bytes(2, "big")
        return [APP_ID, b"device_" + length_prefix + device_bytes]


    # --- STATE MUTATION METHODS (SYNCHRONOUS) ---

    @classmethod
    def execute_transaction(cls, method_name: str, args: list, boxes: list, role: str) -> Dict[str, Any]:
        """
        Generic helper to construct and send a synchronous ATC transaction.
        """
        signer, sender_addr = cls._get_signer_and_address(role)
        sp = client.suggested_params()
        sp.flat_fee = True
        sp.fee = 2000  # Flat 2000 microALGO fee for box operations[cite: 3]

        atc = AtomicTransactionComposer()
        atc.add_method_call(
            app_id=APP_ID,
            method=contract.get_method_by_name(method_name),
            sender=sender_addr,
            sp=sp,
            signer=signer,
            method_args=args,
            boxes=boxes
        )
        
        # Execute synchronously (waits for confirmation)[cite: 3]
        result = atc.execute(client, 4)
        
        return {
            "tx_id": result.tx_ids[0],
            "confirmed_round": result.confirmed_round,
            "status": "success"
        }

    @classmethod
    def register_device(cls, device_id: str, owner_hash: str, config_hash: str) -> Dict[str, Any]:
        """Registers a new device. Operator only[cite: 4]."""
        boxes = [
            cls.get_operator_box(),
            cls.get_device_box(device_id)
        ]
        return cls.execute_transaction("register_device", [device_id, owner_hash, config_hash], boxes, "OPERATOR")

    @classmethod
    def verify_device(cls, device_id: str, confirmed_config_hash: str) -> Dict[str, Any]:
        """Issues a verification stamp. Verifier only[cite: 4]."""
        _, verifier_addr = cls._get_signer_and_address("VERIFIER")
        boxes = [
            cls.get_verifier_box(verifier_addr),
            cls.get_device_box(device_id)
        ]
        return cls.execute_transaction("verify_device", [device_id, confirmed_config_hash], boxes, "VERIFIER")

    @classmethod
    def transfer_device(cls, device_id: str, new_owner_hash: str) -> Dict[str, Any]:
        """Transfers ownership. Verifier only[cite: 4]."""
        _, verifier_addr = cls._get_signer_and_address("VERIFIER")
        boxes = [
            cls.get_verifier_box(verifier_addr),
            cls.get_device_box(device_id)
        ]
        return cls.execute_transaction("transfer_device", [device_id, new_owner_hash], boxes, "VERIFIER")

    @classmethod
    def declare_hardware_change(cls, device_id: str, new_config_hash: str) -> Dict[str, Any]:
        """Logs a hardware upgrade/replacement. Operator only[cite: 4]."""
        boxes = [
            cls.get_operator_box(),
            cls.get_device_box(device_id)
        ]
        return cls.execute_transaction("declare_hardware_change", [device_id, new_config_hash], boxes, "OPERATOR")

    @classmethod
    def raise_dispute(cls, device_id: str, dispute_type: int, raised_by_role: str = "OPERATOR") -> Dict[str, Any]:
        """Raises a dispute. Operator OR Verifier[cite: 3, 4]."""
        _, sender_addr = cls._get_signer_and_address(raised_by_role)
        
        # Contract checks both operator map and verifier map, so both boxes must be provided
        boxes = [
            cls.get_operator_box(),
            cls.get_verifier_box(sender_addr),
            cls.get_device_box(device_id)
        ]
        return cls.execute_transaction("raise_dispute", [device_id, dispute_type], boxes, raised_by_role)

    @classmethod
    def resolve_dispute(cls, device_id: str, resolved_state: int) -> Dict[str, Any]:
        """Resolves a dispute. Admin only[cite: 4]."""
        boxes = [cls.get_device_box(device_id)]
        return cls.execute_transaction("resolve_dispute", [device_id, resolved_state], boxes, "ADMIN")

    @classmethod
    def mark_recycled(cls, device_id: str) -> Dict[str, Any]:
        """Marks device as recycled (terminal). Recycler only[cite: 4]."""
        _, recycler_addr = cls._get_signer_and_address("RECYCLER")
        boxes = [
            cls.get_recycler_box(recycler_addr),
            cls.get_device_box(device_id)
        ]
        return cls.execute_transaction("mark_recycled", [device_id], boxes, "RECYCLER")

    @classmethod
    def mark_exported(cls, device_id: str) -> Dict[str, Any]:
        """Marks device as exported (terminal). Admin only[cite: 3, 4]."""
        boxes = [cls.get_device_box(device_id)]
        return cls.execute_transaction("mark_exported", [device_id], boxes, "ADMIN")


    # --- READ-ONLY METHODS (SIMULATE) ---

    @classmethod
    def _simulate_readonly(cls, method_name: str, args: list, boxes: list) -> Any:
        """
        Executes a read-only ABI call via simulate using allow_unnamed_resources=True[cite: 3].
        Uses the Admin account as a neutral caller.
        """
        signer, sender_addr = cls._get_signer_and_address("ADMIN")
        sp = client.suggested_params()

        atc = AtomicTransactionComposer()
        atc.add_method_call(
            app_id=APP_ID,
            method=contract.get_method_by_name(method_name),
            sender=sender_addr,
            sp=sp,
            signer=signer,
            method_args=args,
            boxes=boxes
        )
        
        sim_req = SimulateRequest(
            txn_groups=[], 
            allow_unnamed_resources=True # Required for zero-fee read-only Box extracts[cite: 3]
        )
        result = atc.simulate(client, sim_req)
        
        if result.failure_message:
            raise Exception(f"Simulation failed: {result.failure_message}")
            
        return result.abi_results[0].return_value

    @classmethod
    def get_device_state(cls, device_id: str) -> Dict[str, Any]:
        """Returns the full parsed DeviceRecord struct from box storage[cite: 4]."""
        boxes = [cls.get_device_box(device_id)]
        raw_state = cls._simulate_readonly("get_device_state", [device_id], boxes)
        
        state_map = {
            1: "REGISTERED",
            2: "VERIFIED",
            3: "TRANSFERRED",
            4: "DISPUTED",
            5: "RECYCLED",
            6: "EXPORTED"
        }
        
        numeric_state = raw_state[0]
        return {
            "state_numeric": numeric_state,
            "state_label": state_map.get(numeric_state, "UNKNOWN"),
            "owner_hash": raw_state[1],
            "config_hash": raw_state[2],
            "stamp_valid": raw_state[3],
            "last_verifier": raw_state[4],
            "updated_at": raw_state[5]
        }

    @classmethod
    def can_verify(cls, device_id: str) -> bool:
        boxes = [cls.get_device_box(device_id)]
        return bool(cls._simulate_readonly("can_verify", [device_id], boxes))

    @classmethod
    def can_transfer(cls, device_id: str) -> bool:
        boxes = [cls.get_device_box(device_id)]
        return bool(cls._simulate_readonly("can_transfer", [device_id], boxes))

    @classmethod
    def can_recycle(cls, device_id: str) -> bool:
        boxes = [cls.get_device_box(device_id)]
        return bool(cls._simulate_readonly("can_recycle", [device_id], boxes))

# Instantiate for easy import in your routes
ledger_service = LedgerService()
