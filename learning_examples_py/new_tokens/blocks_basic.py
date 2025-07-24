"""
Monitors Solana for new LetsBonk token creations using WebSocket blockSubscribe.
Uses a simple hardcoded function to decode 'initialize' instructions from Raydium LaunchLab
based on the IDL structure: MintParams -> CurveParams -> VestingParams.
Requires a Solana RPC WebSocket endpoint for access.

This listener monitors for blocks containing transactions that include both:
- Raydium LaunchLab: LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj
- Let's Bonk Platform Config: FfYek5vEz23cMkWsdJwG2oa6EphsvXSHrGpdALN4g6W1
"""

import asyncio
import base64
import json
import os
import struct
from typing import Dict, Any

import base58
import websockets
from dotenv import load_dotenv
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

load_dotenv()

WSS_ENDPOINT = os.getenv("SOLANA_NODE_WSS_ENDPOINT")

# LetsBonk related program IDs
RAYDIUM_LAUNCHLAB_ID = Pubkey.from_string("LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj")
LETSBONK_PLATFORM_CONFIG_ID = Pubkey.from_string("FfYek5vEz23cMkWsdJwG2oa6EphsvXSHrGpdALN4g6W1")

# Initialize instruction discriminator from IDL (same as geyser_basic.py)
INITIALIZE_DISCRIMINATOR = bytes([175, 175, 109, 31, 13, 152, 155, 237])
INITIALIZE_DISCRIMINATOR_U64 = struct.unpack("<Q", INITIALIZE_DISCRIMINATOR)[0]


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
        return str(keys[account_index])
    
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


def has_letsbonk_accounts(account_keys: list) -> bool:
    """Check if transaction contains both required LetsBonk accounts."""
    account_set = set(str(key) for key in account_keys)
    return (str(RAYDIUM_LAUNCHLAB_ID) in account_set and 
            str(LETSBONK_PLATFORM_CONFIG_ID) in account_set)


async def monitor_letsbonk_blocks():
    """Monitor Solana blockchain for new LetsBonk token creations using blockSubscribe."""
    print("Starting LetsBonk token monitor using WebSocket blockSubscribe")
    print("Monitoring for blocks containing transactions with both:")
    print(f"  - Raydium LaunchLab: {RAYDIUM_LAUNCHLAB_ID}")
    print(f"  - LetsBonk Platform Config: {LETSBONK_PLATFORM_CONFIG_ID}")
    print(f"Connecting to: {WSS_ENDPOINT}")
    
    async with websockets.connect(WSS_ENDPOINT) as websocket:
        # Subscribe to blocks mentioning the Raydium LaunchLab program
        subscription_message = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "blockSubscribe",
            "params": [
                {"mentionsAccountOrProgram": str(RAYDIUM_LAUNCHLAB_ID)},
                {
                    "commitment": "confirmed",
                    "encoding": "base64",
                    "showRewards": False,
                    "transactionDetails": "full",
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        })
        
        await websocket.send(subscription_message)
        print(f"Subscribed to blocks mentioning program: {RAYDIUM_LAUNCHLAB_ID}")
        
        while True:
            try:
                response = await websocket.recv()
                data = json.loads(response)
                
                if "method" in data and data["method"] == "blockNotification":
                    if "params" in data and "result" in data["params"]:
                        block_data = data["params"]["result"]
                        if "value" in block_data and "block" in block_data["value"]:
                            block = block_data["value"]["block"]
                            if "transactions" in block:
                                for tx in block["transactions"]:
                                    if isinstance(tx, dict) and "transaction" in tx:
                                        # Decode base64 transaction data
                                        tx_data_encoded = tx["transaction"][0]
                                        tx_data_decoded = base64.b64decode(tx_data_encoded)
                                        transaction = VersionedTransaction.from_bytes(tx_data_decoded)
                                        
                                        # Check if transaction contains both required accounts
                                        if not has_letsbonk_accounts(transaction.message.account_keys):
                                            continue
                                        
                                        # Get transaction signature
                                        if hasattr(tx, 'signature') and tx['signature']:
                                            signature = tx['signature']
                                        else:
                                            # Fallback: extract signature from transaction
                                            signature = base58.b58encode(bytes(transaction.signatures[0])).decode()
                                        
                                        # Process instructions
                                        for ix in transaction.message.instructions:
                                            program_id = transaction.message.account_keys[ix.program_id_index]
                                            
                                            # Check if instruction is from Raydium LaunchLab
                                            if str(program_id) == str(RAYDIUM_LAUNCHLAB_ID):
                                                ix_data = bytes(ix.data)
                                                
                                                # Check for initialize discriminator
                                                if len(ix_data) >= 8:
                                                    discriminator = struct.unpack("<Q", ix_data[:8])[0]
                                                    
                                                    if discriminator == INITIALIZE_DISCRIMINATOR_U64:
                                                        # Token creation should have substantial data and many accounts
                                                        if len(ix_data) <= 8 or len(ix.accounts) < 10:
                                                            print(f"⚠️  Likely non-creation tx (data: {len(ix_data)}B, accounts: {len(ix.accounts)}) | {signature[:16]}...")
                                                            continue
                                                        
                                                        # Decode the instruction
                                                        try:
                                                            token_info = decode_create_instruction(ix_data, transaction.message.account_keys, ix.accounts)
                                                            print_token_info(token_info, signature)
                                                        except Exception as e:
                                                            print(f"⚠️  Failed to decode instruction: {e}")
                                                            print(f"   Signature: {signature}")
                                                            print(f"   Data length: {len(ix_data)}, Accounts: {len(ix.accounts)}, Keys: {len(transaction.message.account_keys)}")
                                                            print("   " + "-"*60)
                
                elif "result" in data:
                    print("Subscription confirmed")
                else:
                    print(f"Received unexpected message type: {data.get('method', 'Unknown')}")
                    
            except websockets.exceptions.ConnectionClosed:
                print("WebSocket connection closed. Reconnecting...")
                break
            except Exception as e:
                print(f"An error occurred: {e}")
                print(f"Error details: {type(e).__name__}")
                import traceback
                traceback.print_exc()
    
    print("WebSocket connection closed.")


if __name__ == "__main__":
    if not WSS_ENDPOINT:
        print("Error: SOLANA_NODE_WSS_ENDPOINT or SOLANA_WSS_ENDPOINT environment variable not set")
        print("Please set it in your .env file, e.g.: SOLANA_NODE_WSS_ENDPOINT=wss://api.mainnet-beta.solana.com/")
        exit(1)
    
    asyncio.run(monitor_letsbonk_blocks())
