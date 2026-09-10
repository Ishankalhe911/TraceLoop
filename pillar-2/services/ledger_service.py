import os
from typing import Dict, Any, List
from algosdk.v2client import algod
from algosdk import mnemonic, account
from algosdk.abi import Contract
from algosdk.atomic_transaction_composer import (
    AtomicTransactionComposer,
    AccountTransactionSigner,
)
from algosdk.v2client.models import SimulateRequest

class LedgerService:
    def __init__(self):
        # Configuration
        self.app_id = int(os.getenv("APP_ID", "770956001"))
        self.algod_url = os.getenv("ALGOD_URL", "https://testnet-api.algonode.cloud")
        self.algod_token = os.getenv("ALGOD_TOKEN", "")
        
        # Initialize Client
        self.client = algod.AlgodClient(self.algod_token, self.algod_url)
        
        # Harden ABI Path (Resolves absolute path regardless of cwd)
        base_dir = os.path.dirname(os.path.abspath(__file__))
        default_abi_path = os.path.abspath(os.path.join(base_dir, "../../smart_contract/artifacts/contract.json"))
        abi_path = os.getenv("ABI_PATH", default_abi_path)
        
        with open(abi_path, "r") as f:
            self.contract = Contract.from_json(f.read())

    def _get_signer_and_address(self, role: str) -> tuple[AccountTransactionSigner, str]:
        env_map = {
            "OPERATOR": "OPERATOR_MNEMONIC",
            "VERIFIER": "VERIFIER_MNEMONIC",
            "RECYCLER": "RECYCLER_MNEMONIC",
            "ADMIN": "ADMIN_MNEMONIC"
        }
        
        env_key = env_map.get(role.upper())
        if not env_key:
            raise ValueError(f"Invalid role requested for signing: {role}")
            
        mn = os.getenv(env_key, "")
        if not mn:
            raise ValueError(f"Mnemonic for role {role} is not configured in .env")

        pk = mnemonic.to_private_key(mn)
        addr = account.address_from_private_key(pk)
        return AccountTransactionSigner(pk), addr

    def get_operator_box(self) -> List[Any]:
        return [self.app_id, b"operator"]

    def get_verifier_box(self, addr: str) -> List[Any]:
        return [self.app_id, b"verifier_" + account.decode_address(addr)]

    def get_recycler_box(self, addr: str) -> List[Any]:
        return [self.app_id, b"recycler_" + account.decode_address(addr)]

    def get_device_box(self, device_id: str) -> List[Any]:
        device_bytes = device_id.encode("utf-8")
        length_prefix = len(device_bytes).to_bytes(2, "big")
        return [self.app_id, b"device_" + length_prefix + device_bytes]

    def execute_transaction(self, method_name: str, args: list, boxes: list, role: str) -> Dict[str, Any]:
        signer, sender_addr = self._get_signer_and_address(role)
        sp = self.client.suggested_params()
        sp.flat_fee = True
        sp.fee = 2000 

        atc = AtomicTransactionComposer()
        atc.add_method_call(
            app_id=self.app_id,
            method=self.contract.get_method_by_name(method_name),
            sender=sender_addr,
            sp=sp,
            signer=signer,
            method_args=args,
            boxes=boxes
        )
        
        result = atc.execute(self.client, 4)
        return {
            "tx_id": result.tx_ids[0],
            "confirmed_round": result.confirmed_round,
            "status": "success"
        }

    def register_device(self, device_id: str, owner_hash: str, config_hash: str) -> Dict[str, Any]:
        boxes = [self.get_operator_box(), self.get_device_box(device_id)]
        return self.execute_transaction("register_device", [device_id, owner_hash, config_hash], boxes, "OPERATOR")

    def verify_device(self, device_id: str, confirmed_config_hash: str) -> Dict[str, Any]:
        _, verifier_addr = self._get_signer_and_address("VERIFIER")
        boxes = [self.get_verifier_box(verifier_addr), self.get_device_box(device_id)]
        return self.execute_transaction("verify_device", [device_id, confirmed_config_hash], boxes, "VERIFIER")

    def transfer_device(self, device_id: str, new_owner_hash: str) -> Dict[str, Any]:
        _, verifier_addr = self._get_signer_and_address("VERIFIER")
        boxes = [self.get_verifier_box(verifier_addr), self.get_device_box(device_id)]
        return self.execute_transaction("transfer_device", [device_id, new_owner_hash], boxes, "VERIFIER")

    def declare_hardware_change(self, device_id: str, new_config_hash: str) -> Dict[str, Any]:
        boxes = [self.get_operator_box(), self.get_device_box(device_id)]
        return self.execute_transaction("declare_hardware_change", [device_id, new_config_hash], boxes, "OPERATOR")

    def raise_dispute(self, device_id: str, dispute_type: int, raised_by_role: str = "OPERATOR") -> Dict[str, Any]:
        _, sender_addr = self._get_signer_and_address(raised_by_role)
        boxes = [self.get_device_box(device_id)]
        
        # Optimized conditional box reference
        if raised_by_role.upper() == "OPERATOR":
            boxes.insert(0, self.get_operator_box())
        else:
            boxes.insert(0, self.get_verifier_box(sender_addr))
            
        return self.execute_transaction("raise_dispute", [device_id, dispute_type], boxes, raised_by_role)

    def resolve_dispute(self, device_id: str, resolved_state: int) -> Dict[str, Any]:
        boxes = [self.get_device_box(device_id)]
        return self.execute_transaction("resolve_dispute", [device_id, resolved_state], boxes, "ADMIN")

    def mark_recycled(self, device_id: str) -> Dict[str, Any]:
        _, recycler_addr = self._get_signer_and_address("RECYCLER")
        boxes = [self.get_recycler_box(recycler_addr), self.get_device_box(device_id)]
        return self.execute_transaction("mark_recycled", [device_id], boxes, "RECYCLER")

    def mark_exported(self, device_id: str) -> Dict[str, Any]:
        boxes = [self.get_device_box(device_id)]
        return self.execute_transaction("mark_exported", [device_id], boxes, "ADMIN")

    def _simulate_readonly(self, method_name: str, args: list, boxes: list) -> Any:
        signer, sender_addr = self._get_signer_and_address("ADMIN")
        sp = self.client.suggested_params()

        atc = AtomicTransactionComposer()
        atc.add_method_call(
            app_id=self.app_id,
            method=self.contract.get_method_by_name(method_name),
            sender=sender_addr,
            sp=sp,
            signer=signer,
            method_args=args,
            boxes=boxes
        )
        
        sim_req = SimulateRequest(txn_groups=[], allow_unnamed_resources=True)
        result = atc.simulate(self.client, sim_req)
        
        if result.failure_message:
            raise Exception(f"Simulation failed: {result.failure_message}")
        return result.abi_results[0].return_value

    def get_device_state(self, device_id: str) -> Dict[str, Any]:
        boxes = [self.get_device_box(device_id)]
        raw_state = self._simulate_readonly("get_device_state", [device_id], boxes)
        
        state_map = {1: "REGISTERED", 2: "VERIFIED", 3: "TRANSFERRED", 4: "DISPUTED", 5: "RECYCLED", 6: "EXPORTED"}
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

    def can_verify(self, device_id: str) -> bool:
        return bool(self._simulate_readonly("can_verify", [device_id], [self.get_device_box(device_id)]))

    def can_transfer(self, device_id: str) -> bool:
        return bool(self._simulate_readonly("can_transfer", [device_id], [self.get_device_box(device_id)]))

    def can_recycle(self, device_id: str) -> bool:
        return bool(self._simulate_readonly("can_recycle", [device_id], [self.get_device_box(device_id)]))

ledger_service = LedgerService()
