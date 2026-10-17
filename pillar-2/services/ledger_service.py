import os
import json
from typing import Dict, Any, List

from algosdk.v2client import algod
from algosdk import mnemonic, account
from algosdk.encoding import decode_address
from algosdk.abi import Contract
from algosdk.atomic_transaction_composer import (
    AtomicTransactionComposer,
    AccountTransactionSigner,
)
from algosdk.v2client.models import SimulateRequest

from services.device_identity import validate_public_id


class LedgerSubmissionError(RuntimeError):
    """Safe error carrying whether an Algorand submission may have occurred.

    The route uses this flag only to choose FAILED (known not submitted) versus
    RECONCILIATION_REQUIRED (possibly submitted). Never expose exception text to
    API callers because SDK errors can contain request details.
    """

    def __init__(self, error_code: str, *, submission_attempted: bool):
        self.error_code = error_code
        self.submission_attempted = submission_attempted
        message = (
            "Ledger submission outcome is uncertain."
            if submission_attempted
            else "Ledger transaction was not submitted."
        )
        super().__init__(message)


class LedgerService:
    def __init__(self):
        # Configuration
        self.app_id = int(os.getenv("APP_ID", "771258340"))
        self.algod_url = os.getenv("ALGOD_URL", "https://testnet-api.algonode.cloud")
        self.algod_token = os.getenv("ALGOD_TOKEN", "")

        # Initialize Client
        self.client = algod.AlgodClient(self.algod_token, self.algod_url)

        # Resolve the ABI path independently of the process working directory.
        base_dir = os.path.dirname(os.path.abspath(__file__))
        default_abi_path = os.path.abspath(
            os.path.join(base_dir, "../../smart_contract/artifacts/contract.json")
        )
        abi_path = os.getenv("ABI_PATH", default_abi_path)

        # The artifact is ARC-56; algosdk Contract.from_json expects ARC-4.
        with open(abi_path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        arc4_contract = {
            "name": raw["name"],
            "methods": [
                {
                    "name": method["name"],
                    "args": method.get("args", []),
                    "returns": method.get("returns", {"type": "void"}),
                    "desc": method.get("desc", ""),
                }
                for method in raw["methods"]
            ],
            "desc": raw.get("desc", ""),
        }
        self.contract = Contract.from_json(json.dumps(arc4_contract))

    def _get_signer_and_address(self, role: str) -> tuple[AccountTransactionSigner, str]:
        env_map = {
            "OPERATOR": "OPERATOR_MNEMONIC",
            "VERIFIER": "VERIFIER_MNEMONIC",
            "RECYCLER": "RECYCLER_MNEMONIC",
            "ADMIN": "ADMIN_MNEMONIC",
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
        return [self.app_id, b"verifier_" + decode_address(addr)]

    def get_recycler_box(self, addr: str) -> List[Any]:
        return [self.app_id, b"recycler_" + decode_address(addr)]

    def get_device_box(self, public_id: str) -> List[Any]:
        """Return the app box reference for a validated public Passport ID."""
        public_id = validate_public_id(public_id)
        device_bytes = public_id.encode("utf-8")
        length_prefix = len(device_bytes).to_bytes(2, "big")
        return [self.app_id, b"device_" + length_prefix + device_bytes]

    def execute_transaction(self, method_name: str, args: list, boxes: list, role: str) -> Dict[str, Any]:
        # Every failure before atc.execute() is a definite pre-submission failure:
        # no transaction has been handed to the SDK for broadcast yet.
        try:
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
                boxes=boxes,
            )
        except Exception as exc:
            raise LedgerSubmissionError(
                "LEDGER_PRE_SUBMISSION_FAILED", submission_attempted=False
            ) from exc

        # Once execute() starts, a timeout/error may happen after broadcast. Be
        # conservative and require reconciliation rather than issuing a new tx.
        try:
            result = atc.execute(self.client, 4)
        except Exception as exc:
            raise LedgerSubmissionError(
                "LEDGER_OUTCOME_UNKNOWN", submission_attempted=True
            ) from exc

        return {
            "tx_id": result.tx_ids[0],
            "confirmed_round": result.confirmed_round,
            "status": "success",
        }

    def register_device(
        self, public_id: str, owner_hash: str, config_hash: str
    ) -> Dict[str, Any]:
        try:
            public_id = validate_public_id(public_id)
            boxes = [self.get_operator_box(), self.get_device_box(public_id)]
        except Exception as exc:
            # Validation/box creation happens before a transaction can be submitted.
            raise LedgerSubmissionError(
                "LEDGER_PRE_SUBMISSION_FAILED", submission_attempted=False
            ) from exc
        return self.execute_transaction(
            "register_device", [public_id, owner_hash, config_hash], boxes, "OPERATOR"
        )

    def verify_device(self, public_id: str, confirmed_config_hash: str) -> Dict[str, Any]:
        public_id = validate_public_id(public_id)
        _, verifier_addr = self._get_signer_and_address("VERIFIER")
        boxes = [self.get_verifier_box(verifier_addr), self.get_device_box(public_id)]
        return self.execute_transaction(
            "verify_device", [public_id, confirmed_config_hash], boxes, "VERIFIER"
        )

    def transfer_device(self, public_id: str, new_owner_hash: str) -> Dict[str, Any]:
        public_id = validate_public_id(public_id)
        _, verifier_addr = self._get_signer_and_address("VERIFIER")
        boxes = [self.get_verifier_box(verifier_addr), self.get_device_box(public_id)]
        return self.execute_transaction(
            "transfer_device", [public_id, new_owner_hash], boxes, "VERIFIER"
        )

    def declare_hardware_change(self, public_id: str, new_config_hash: str) -> Dict[str, Any]:
        public_id = validate_public_id(public_id)
        boxes = [self.get_operator_box(), self.get_device_box(public_id)]
        return self.execute_transaction(
            "declare_hardware_change", [public_id, new_config_hash], boxes, "OPERATOR"
        )

    def raise_dispute(
        self, public_id: str, dispute_type: int, raised_by_role: str = "OPERATOR"
    ) -> Dict[str, Any]:
        public_id = validate_public_id(public_id)
        _, sender_addr = self._get_signer_and_address(raised_by_role)
        boxes = [
            self.get_operator_box(),
            self.get_verifier_box(sender_addr),
            self.get_device_box(public_id),
        ]
        return self.execute_transaction(
            "raise_dispute", [public_id, dispute_type], boxes, raised_by_role
        )

    def resolve_dispute(self, public_id: str, resolved_state: int) -> Dict[str, Any]:
        public_id = validate_public_id(public_id)
        boxes = [self.get_device_box(public_id)]
        return self.execute_transaction(
            "resolve_dispute", [public_id, resolved_state], boxes, "ADMIN"
        )

    def mark_recycled(self, public_id: str) -> Dict[str, Any]:
        public_id = validate_public_id(public_id)
        _, recycler_addr = self._get_signer_and_address("RECYCLER")
        boxes = [self.get_recycler_box(recycler_addr), self.get_device_box(public_id)]
        return self.execute_transaction("mark_recycled", [public_id], boxes, "RECYCLER")

    def mark_exported(self, public_id: str) -> Dict[str, Any]:
        public_id = validate_public_id(public_id)
        boxes = [self.get_device_box(public_id)]
        return self.execute_transaction("mark_exported", [public_id], boxes, "ADMIN")

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
            boxes=boxes,
        )

        sim_req = SimulateRequest(txn_groups=[], allow_unnamed_resources=True)
        result = atc.simulate(self.client, sim_req)

        if result.failure_message:
            raise Exception(f"Simulation failed: {result.failure_message}")
        return result.abi_results[0].return_value

    def get_device_state(self, public_id: str) -> Dict[str, Any]:
        public_id = validate_public_id(public_id)
        boxes = [self.get_device_box(public_id)]
        raw_state = self._simulate_readonly("get_device_state", [public_id], boxes)

        state_map = {
            1: "REGISTERED",
            2: "VERIFIED",
            3: "TRANSFERRED",
            4: "DISPUTED",
            5: "RECYCLED",
            6: "EXPORTED",
        }
        numeric_state = raw_state[0]

        return {
            "state_numeric": numeric_state,
            "state_label": state_map.get(numeric_state, "UNKNOWN"),
            "owner_hash": raw_state[1],
            "config_hash": raw_state[2],
            "stamp_valid": raw_state[3],
            "last_verifier": raw_state[4],
            "updated_at": raw_state[5],
        }

    def can_verify(self, public_id: str) -> bool:
        public_id = validate_public_id(public_id)
        return bool(
            self._simulate_readonly(
                "can_verify", [public_id], [self.get_device_box(public_id)]
            )
        )

    def can_transfer(self, public_id: str) -> bool:
        public_id = validate_public_id(public_id)
        return bool(
            self._simulate_readonly(
                "can_transfer", [public_id], [self.get_device_box(public_id)]
            )
        )

    def can_recycle(self, public_id: str) -> bool:
        public_id = validate_public_id(public_id)
        return bool(
            self._simulate_readonly(
                "can_recycle", [public_id], [self.get_device_box(public_id)]
            )
        )


ledger_service = LedgerService()