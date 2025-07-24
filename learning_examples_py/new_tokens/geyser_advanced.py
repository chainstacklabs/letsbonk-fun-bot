"""
Monitors Solana for new LetsBonk token creations using Geyser gRPC.
Decodes 'initialize' instructions from Raydium LaunchLab to extract and display token details.
Requires a Geyser API token for access.
Supports both Basic and X-Token authentication methods.

This listener monitors for transactions that include both:
- Raydium LaunchLab: LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj
- Let's Bonk Platform Config: FfYek5vEz23cMkWsdJwG2oa6EphsvXSHrGpdALN4g6W1
"""

import asyncio
import os
from typing import Dict, Any
import sys

import base58
import grpc
from dotenv import load_dotenv
from solders.pubkey import Pubkey

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from generated import geyser_pb2, geyser_pb2_grpc
from learning_examples_py.idl_parser import load_idl_parser


load_dotenv()

GEYSER_ENDPOINT = os.getenv("GEYSER_ENDPOINT")
GEYSER_API_TOKEN = os.getenv("GEYSER_API_TOKEN")
# Default to x-token auth, can be set to "basic"
AUTH_TYPE = "x-token"

# LetsBonk related program IDs
RAYDIUM_LAUNCHLAB_ID = Pubkey.from_string("LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj")
LETSBONK_PLATFORM_CONFIG_ID = Pubkey.from_string("FfYek5vEz23cMkWsdJwG2oa6EphsvXSHrGpdALN4g6W1")

# Initialize instruction discriminator from IDL
INITIALIZE_DISCRIMINATOR = bytes([175, 175, 109, 31, 13, 152, 155, 237])


async def create_geyser_connection():
    """Establish a secure connection to the Geyser endpoint using the configured auth type."""
    if AUTH_TYPE == "x-token":
        auth = grpc.metadata_call_credentials(
            lambda _, callback: callback((("x-token", GEYSER_API_TOKEN),), None)
        )
    else:  # Default to basic auth
        auth = grpc.metadata_call_credentials(
            lambda _, callback: callback((("authorization", f"Basic {GEYSER_API_TOKEN}"),), None)
        )
    
    creds = grpc.composite_channel_credentials(grpc.ssl_channel_credentials(), auth)
    channel = grpc.aio.secure_channel(GEYSER_ENDPOINT, creds)
    return geyser_pb2_grpc.GeyserStub(channel)


def create_subscription_request():
    """Create a subscription request for LetsBonk transactions."""
    request = geyser_pb2.SubscribeRequest()
    # Monitor transactions that include both Raydium LaunchLab and LetsBonk Platform Config
    request.transactions["letsbonk_filter"].account_required.append(str(RAYDIUM_LAUNCHLAB_ID))
    request.transactions["letsbonk_filter"].account_required.append(str(LETSBONK_PLATFORM_CONFIG_ID))
    request.transactions["letsbonk_filter"].failed = False
    request.transactions["letsbonk_filter"].vote = False
    request.commitment = geyser_pb2.CommitmentLevel.PROCESSED
    return request


def print_token_info(decoded_data: Dict[str, Any], signature: str):
    """Print formatted token information in a compact format."""
    if 'args' not in decoded_data or 'base_mint_param' not in decoded_data['args']:
        print("⚠️  Could not extract token information")
        return
    
    mint_params = decoded_data['args']['base_mint_param']
    accounts = decoded_data['accounts']
    
    print(f"\n🚀 NEW TOKEN: {mint_params.get('name', 'N/A')} ({mint_params.get('symbol', 'N/A')})")
    print(f"   Signature: {signature}")
    print(f"   Creator: {accounts.get('creator', 'N/A')}")
    print(f"   Base Mint: {accounts.get('base_mint', 'N/A')}")
    print(f"   Pool: {accounts.get('pool_state', 'N/A')}")
    if mint_params.get('uri'):
        print(f"   Metadata: {mint_params['uri']}")
    print("   " + "="*60)


async def monitor_letsbonk():
    """Monitor Solana blockchain for new LetsBonk token creations."""
    print(f"Starting LetsBonk token monitor using {AUTH_TYPE.upper()} authentication")
    print("Monitoring for transactions containing both:")
    print(f"  - Raydium LaunchLab: {RAYDIUM_LAUNCHLAB_ID}")
    print(f"  - LetsBonk Platform Config: {LETSBONK_PLATFORM_CONFIG_ID}")
    
    # Initialize IDL parser
    idl_path = "idl/raydium_launchlab.json"
    parser = load_idl_parser(idl_path, verbose=True)
    
    stub = await create_geyser_connection()
    request = create_subscription_request()
    
    async for update in stub.Subscribe(iter([request])):        
        tx = update.transaction.transaction.transaction
        msg = getattr(tx, "message", None)
        if msg is None:
            continue
        
        for _, ix in enumerate(msg.instructions):
            if not ix.data.startswith(INITIALIZE_DISCRIMINATOR):
                continue

            signature = base58.b58encode(bytes(update.transaction.transaction.signature)).decode()
            
            # Validate basic instruction data length
            if len(ix.data) < 8:
                print(f"⚠️  Short instruction data - likely not token creation | {signature}")
                continue
            
            # Decode the instruction using IDL
            decoded_data = parser.decode_instruction(ix.data, msg.account_keys, ix.accounts)
            if decoded_data and decoded_data['instruction_name'] == 'initialize':
                print_token_info(decoded_data, signature)
            elif not decoded_data:
                print(f"⚠️  Failed to decode - likely not token creation | {signature}")


if __name__ == "__main__":
    asyncio.run(monitor_letsbonk())
