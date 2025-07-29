""" 
Listens for Raydium Launchpad program logs. Get the transaction details using the signature.
Parse the token mint from the MigrateToCpswap instruction.

Note: Since we are listening to the Launchpad logs, it will consume a lot of credits/compute units.
This scripts finds the all tokens that are migrated to the CPSwap program, not limited to only bonkfun.

"""


import json
import asyncio
import os

from dotenv import load_dotenv
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey
from solana.rpc.async_api import AsyncClient
from solders.signature import Signature
import websockets

load_dotenv()

RPC_ENDPOINT = os.environ.get("SOLANA_NODE_RPC_ENDPOINT")
WSS_ENDPOINT = os.environ.get("SOLANA_NODE_WSS_ENDPOINT")
RAYDIUM_LAUNCHPAD_PROGRAM_ID = Pubkey.from_string("LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj")


def is_transaction_successful(logs):
    for log in logs:
        if "AnchorError thrown" in log or "Error" in log:
            print(f"[ERROR] Transaction failed: {log}")
            return False
    return True


async def process_transaction(signature: str):
    client = AsyncClient(RPC_ENDPOINT)
    signature = Signature.from_string(signature)

    try:
        resp = await client.get_transaction(
            signature,
            encoding="jsonParsed",
            commitment="confirmed",
            max_supported_transaction_version=0,
        )
    except Exception as e:
        print(f"[ERROR] Failed to get transaction data time: {e}")
        return
    
    # retrying if the node is not fully synced
    if not resp.value:
        await asyncio.sleep(5)
        resp = await client.get_transaction(
            signature,
            encoding="jsonParsed",
            commitment="confirmed",
            max_supported_transaction_version=0,
        )
        
        if not resp.value:
            print(f"[ERROR] Transaction not found: {signature}")
            return
        
    instructions = resp.value.transaction.transaction.message.instructions
    
    for instruction in instructions:
        if instruction.program_id == RAYDIUM_LAUNCHPAD_PROGRAM_ID and instruction.data == "PotQtwz6wf1":
            if len(instruction.accounts) == 38:
                token_mint = instruction.accounts[1]
                print(f"[INFO] Token migrated to cpswap: {token_mint}")
                # TODO : use the idl parser and get the more details for the pool and token
                break
            

async def listen_for_migrations():
    while True:
        try:
            print("\n[INFO] Connecting to WebSocket ...")
            async with websockets.connect(WSS_ENDPOINT) as websocket:
                subscription_message = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "logsSubscribe",
                        "params": [
                            {"mentions": [str(RAYDIUM_LAUNCHPAD_PROGRAM_ID)]},
                            {"commitment": "confirmed"},
                        ],
                    }
                )
                await websocket.send(subscription_message)
                print(
                    f"[INFO] Listening for migration instructions from program: {RAYDIUM_LAUNCHPAD_PROGRAM_ID}"
                )

                response = await websocket.recv()
                print(f"[INFO] Subscription response: {response}")

                while True:
                    try:
                        response = await asyncio.wait_for(websocket.recv(), timeout=60)
                        data = json.loads(response)
                        log_data = data["params"]["result"]["value"]
                        logs = log_data.get("logs", [])
                        signature = log_data.get("signature", "unknown")

                        is_migrated = any(
                            "Program log: Instruction: MigrateToCpswap" == log
                            for log in logs
                        )
                        if not is_migrated:
                            continue

                        asyncio.create_task(process_transaction(signature))

                    except TimeoutError:
                        print("[INFO] No new messages received, continuing...")
                    except Exception as e:
                        print(f"[ERROR] Error receiving message: {e}")
                        break

        except Exception as e:
            print(f"[ERROR] Connection error: {e}")
            print("[INFO] Retrying in 5 seconds...")
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(listen_for_migrations())