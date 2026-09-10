import os
from dotenv import load_dotenv
from algokit_utils import AlgorandClient
from traceloop_client import TraceLoopLedgerFactory

def main():
    # Load the .env file containing your DEPLOYER_MNEMONIC
    load_dotenv()

    # 1. Connect directly to Algorand TestNet (Corrected method name)
    algorand = AlgorandClient.testnet()

    # 2. Load your funded TestNet account securely from the environment
    # This automatically looks for DEPLOYER_MNEMONIC in your .env file
    deployer = algorand.account.from_environment("DEPLOYER")
    print(f"Initiating deployment with account: {deployer.address}")

    # 3. Initialize the Factory using the generated client
    factory = TraceLoopLedgerFactory(
        algorand=algorand,
        default_sender=deployer.address,
        default_signer=deployer.signer,
    )

    # 4. Deploy the contract
    print("Deploying Trace-Loop Ledger to TestNet...")
    client, result = factory.deploy(
        on_schema_break="append", # Safe for dev: replaces contract if state logic changes
        on_update="append",
    )

    print(f"✅ Success! Contract deployed.")
    print(f"App ID: {client.app_id}")
    print(f"App Address: {client.app_address}")
    print(f"View live on Lora: https://lora.algokit.io/testnet/application/{client.app_id}")

if __name__ == "__main__":
    main()