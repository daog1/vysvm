#!/usr/bin/env python3
"""Deploy and test the fixed VyperSVM program."""

import subprocess
import sys
import time
from solana.rpc.api import Client
from solana.rpc.commitment import Confirmed
from solders.pubkey import Pubkey
import os


def deploy_program():
    """Deploy the hello_world.so program to Solana."""
    print("Deploying the fixed program...")

    # Use the same keypair as in the test
    keypair_path = os.path.expanduser("~/.config/solana/id.json")

    try:
        # Deploy using solana CLI
        cmd = [
            "solana",
            "program",
            "deploy",
            "hello_world.so",
            "--keypair",
            keypair_path,
            "--url",
            "http://localhost:8899",
            "--use-rpc",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, cwd=".")
        print("Deployment output:")
        print(result.stdout)
        if result.stderr:
            print("Deployment errors:")
            print(result.stderr)

        if result.returncode != 0:
            print(f"Deployment failed with return code {result.returncode}")
            return None

        # Extract program ID from output
        for line in result.stdout.split("\n"):
            if "Program Id:" in line:
                program_id = line.split("Program Id:")[1].strip()
                print(f"Program deployed with ID: {program_id}")
                return program_id

        print("Could not extract program ID from deployment output")
        return None

    except Exception as e:
        print(f"Deployment failed: {e}")
        return None


def test_program(program_id):
    """Test the deployed program."""
    print(f"\nTesting program {program_id}...")

    try:
        client = Client("http://localhost:8899", commitment=Confirmed)
        program_pubkey = Pubkey.from_string(program_id)

        # Check if program exists
        account_info = client.get_account_info(program_pubkey)
        if account_info.value is None:
            print("Program account not found")
            return False

        print(f"Program exists: {account_info.value.executable}")
        print(f"Program balance: {account_info.value.lamports}")
        print(f"Program owner: {account_info.value.owner}")

        return True

    except Exception as e:
        print(f"Test failed: {e}")
        return False


def invoke_program(program_id, message="00"):
    """Invoke the deployed program using the existing invoke.js helper."""
    print(f"\nInvoking program {program_id} with message {message!r}...")

    cmd = ["node", "invoke2.js", program_id]
    if message:
        cmd.extend(["--message", message])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=".",
        )
        print("Invocation output:")
        print(result.stdout)
        if result.stderr:
            print("Invocation errors:")
            print(result.stderr)

        if result.returncode != 0:
            print(f"Invocation failed with return code {result.returncode}")
            return False

        print("Program invoked successfully.")
        return True

    except subprocess.TimeoutExpired:
        print("Invocation timed out - possible runtime issue.")
        return False
    except Exception as e:
        print(f"Invocation failed: {e}")
        return False


def main():
    print("Testing VyperSVM fixes...")
    print("=" * 50)

    # Deploy the program
    program_id = deploy_program()
    if not program_id:
        print("Failed to deploy program")
        return 1

    # Test the program
    success = test_program(program_id)
    if not success:
        print("Program test failed")
        return 1

    # Invoke the program after successful deployment
    invoked = invoke_program(program_id)
    if not invoked:
        print("Program invocation failed")
        return 1

    print("\nProgram deployed and tested successfully!")
    print(f"Use this program ID with invoke.js: {program_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
