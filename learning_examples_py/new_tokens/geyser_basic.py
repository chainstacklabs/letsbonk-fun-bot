"""
Monitors Solana for new LetsBonk token creations using Geyser gRPC.
Uses a simple hardcoded function to decode 'initialize' instructions from Raydium LaunchLab
based on the IDL structure: MintParams -> CurveParams -> VestingParams.
Requires a Geyser API token for access.
Supports both Basic and X-Token authentication methods.

This listener monitors for transactions that include both:
- Raydium LaunchLab: LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj
- Let's Bonk Platform Config: FfYek5vEz23cMkWsdJwG2oa6EphsvXSHrGpdALN4g6W1
"""

import asyncio
import os
import struct
from typing import Dict, Any
import sys

import base58
import grpc
from dotenv import load_dotenv
from solders.pubkey import Pubkey

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from generated import geyser_pb2, geyser_pb2_grpc


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


def decode_create_instruction(ix_data: bytes, keys, accounts) -> dict:
    """Decode a create instruction from transaction data based on IDL structure."""
    # Skip past the 8-byte discriminator prefix
    offset = 8
    
    # Extract account keys in base58 format
    def get_account_key(index):
        if index >= len(accounts):
            return "N/A"
        account_index = accounts[index]
        if account_index >= len(keys):
            return "N/A"
        return base58.b58encode(keys[account_index]).decode()
    
    # Read string fields (prefixed with length)
    def read_string():
        nonlocal offset
        if offset + 4 > len(ix_data):
            raise ValueError(f"Not enough data for string length at offset {offset}")
        # Get string length (4-byte uint)
        length = struct.unpack_from("<I", ix_data, offset)[0]
        offset += 4
        if offset + length > len(ix_data):
            raise ValueError(f"Not enough data for string of length {length} at offset {offset}")
        # Extract and decode the string
        value = ix_data[offset:offset + length].decode()
        offset += length
        return value
    
    def read_u8():
        nonlocal offset
        if offset + 1 > len(ix_data):
            raise ValueError(f"Not enough data for u8 at offset {offset}")
        value = struct.unpack_from("<B", ix_data, offset)[0]
        offset += 1
        return value
    
    def read_u64():
        nonlocal offset
        if offset + 8 > len(ix_data):
            raise ValueError(f"Not enough data for u64 at offset {offset}")
        value = struct.unpack_from("<Q", ix_data, offset)[0]
        offset += 8
        return value
    
    # Parse base_mint_param (MintParams struct)
    decimals = read_u8()
    name = read_string()
    symbol = read_string()
    uri = read_string()
    
    # Parse curve_param (CurveParams enum)
    curve_variant = read_u8()  # enum discriminator
    
    # Skip curve data based on variant
    if curve_variant == 0:  # Constant
        # ConstantCurve: supply(u64), total_base_sell(u64), total_quote_fund_raising(u64), migrate_type(u8)
        skip_bytes = 8 + 8 + 8 + 1  # 25 bytes
        if offset + skip_bytes > len(ix_data):
            raise ValueError(f"Not enough data for ConstantCurve at offset {offset}")
        offset += skip_bytes
    elif curve_variant == 1:  # Fixed
        # FixedCurve: supply(u64), total_quote_fund_raising(u64), migrate_type(u8)
        skip_bytes = 8 + 8 + 1  # 17 bytes
        if offset + skip_bytes > len(ix_data):
            raise ValueError(f"Not enough data for FixedCurve at offset {offset}")
        offset += skip_bytes
    elif curve_variant == 2:  # Linear
        # LinearCurve: supply(u64), total_quote_fund_raising(u64), migrate_type(u8)
        skip_bytes = 8 + 8 + 1  # 17 bytes
        if offset + skip_bytes > len(ix_data):
            raise ValueError(f"Not enough data for LinearCurve at offset {offset}")
        offset += skip_bytes
    else:
        # Unknown variant, try to continue but might fail
        raise ValueError(f"Unknown curve variant: {curve_variant}")
    
    # Parse vesting_param (VestingParams struct)
    total_locked_amount = read_u64()
    cliff_period = read_u64()
    unlock_period = read_u64()
    
    token_info = {
        "name": name,
        "symbol": symbol,
        "uri": uri,
        "decimals": decimals,
        "total_locked_amount": total_locked_amount,
        "cliff_period": cliff_period,
        "unlock_period": unlock_period,
        "curve_variant": curve_variant,
        # Account mappings based on IDL
        "payer": get_account_key(0),
        "creator": get_account_key(1),
        "global_config": get_account_key(2),
        "platform_config": get_account_key(3),
        "authority": get_account_key(4),
        "pool_state": get_account_key(5),
        "base_mint": get_account_key(6),
        "quote_mint": get_account_key(7),
        "base_vault": get_account_key(8),
        "quote_vault": get_account_key(9),
        "metadata_account": get_account_key(10),
        "base_token_program": get_account_key(11),
        "quote_token_program": get_account_key(12),
        "metadata_program": get_account_key(13),
        "system_program": get_account_key(14),
        "rent_program": get_account_key(15),
    }
        
    return token_info


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


def print_token_info(token_info: Dict[str, Any], signature: str):
    """Print formatted token information in a compact format."""
    print(f"\n🚀 NEW TOKEN: {token_info.get('name', 'N/A')} ({token_info.get('symbol', 'N/A')})")
    print(f"   Signature: {signature}")
    print(f"   Creator: {token_info.get('creator', 'N/A')}")
    print(f"   Base Mint: {token_info.get('base_mint', 'N/A')}")
    print(f"   Pool State: {token_info.get('pool_state', 'N/A')}")
    print(f"   Metadata: {token_info.get('metadata_account', 'N/A')}")
    if token_info.get('uri'):
        print(f"   Metadata URI: {token_info['uri']}")
    print("   " + "="*60)


async def monitor_letsbonk():
    """Monitor Solana blockchain for new LetsBonk token creations."""
    print(f"Starting LetsBonk token monitor using {AUTH_TYPE.upper()} authentication")
    print("Monitoring for transactions containing both:")
    print(f"  - Raydium LaunchLab: {RAYDIUM_LAUNCHLAB_ID}")
    print(f"  - LetsBonk Platform Config: {LETSBONK_PLATFORM_CONFIG_ID}")
    
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
            
            # Token creation should have substantial data and many accounts
            if len(ix.data) <= 8 or len(ix.accounts) < 10:
                print(f"⚠️  Likely non-creation tx (data: {len(ix.data)}B, accounts: {len(ix.accounts)}) | {signature[:16]}...")
                continue
            
            # Decode the instruction using simple parsing
            try:
                token_info = decode_create_instruction(ix.data, msg.account_keys, ix.accounts)
                print_token_info(token_info, signature)
            except Exception as e:
                print(f"⚠️  Failed to decode instruction: {e}")
                print(f"   Signature: {signature}")
                print(f"   Data length: {len(ix.data)}, Accounts: {len(ix.accounts)}, Keys: {len(msg.account_keys)}")
                print("   " + "-"*60)


if __name__ == "__main__":
    asyncio.run(monitor_letsbonk())
