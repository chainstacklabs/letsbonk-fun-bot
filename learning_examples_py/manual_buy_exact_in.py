"""
Manual Buy Exact In Example for Raydium LaunchLab

This script demonstrates how to buy tokens using the buy_exact_in instruction
from the Raydium LaunchLab program. It follows the IDL structure and implements
the same transaction pattern shown in the Solscan example.

Key features:
- Uses buy_exact_in instruction (discriminator: [250, 234, 13, 123, 213, 156, 19, 236])
- Implements proper account ordering as per IDL
- Includes slippage protection with minimum_amount_out
- Handles WSOL wrapping/unwrapping automatically
- Follows the exact transaction structure from the Solscan example
- User configurable SOL amount and slippage
- Uses idempotent ATA creation
"""

import asyncio
import os
import struct
import sys
from typing import Optional

import base58
from dotenv import load_dotenv
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solana.rpc.types import TxOpts
from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.system_program import CreateAccountWithSeedParams, create_account_with_seed
from solders.transaction import VersionedTransaction

load_dotenv()

TOKEN_MINT_ADDRESS = Pubkey.from_string("CKyveMBB55WkfZrELaUWnA3R74RTQEmLYhi8m3v4bonk")

# Configuration constants
RPC_ENDPOINT = os.environ.get("SOLANA_NODE_RPC_ENDPOINT")
PRIVATE_KEY = base58.b58decode(os.environ.get("SOLANA_PRIVATE_KEY"))
PAYER = Keypair.from_bytes(PRIVATE_KEY)

# User configurable parameters
SOL_AMOUNT_TO_SPEND = float(os.environ.get("SOL_AMOUNT", "0.001"))
SLIPPAGE_TOLERANCE = float(os.environ.get("SLIPPAGE", "0.25"))

# Transaction parameters
SHARE_FEE_RATE = 0

# Program IDs and addresses from Raydium LaunchLab
RAYDIUM_LAUNCHLAB_PROGRAM_ID = Pubkey.from_string("LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj")
GLOBAL_CONFIG = Pubkey.from_string("6s1xP3hpbAfFoNtUNF8mfHsjr2Bd97JxFJRWLbL6aHuX")
LETSBONK_PLATFORM_CONFIG = Pubkey.from_string("FfYek5vEz23cMkWsdJwG2oa6EphsvXSHrGpdALN4g6W1")

# Token program and system addresses
TOKEN_PROGRAM_ID = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
SYSTEM_PROGRAM_ID = Pubkey.from_string("11111111111111111111111111111111")
WSOL_MINT = Pubkey.from_string("So11111111111111111111111111111111111111112")
COMPUTE_BUDGET_PROGRAM_ID = Pubkey.from_string("ComputeBudget111111111111111111111111111111")
ASSOCIATED_TOKEN_PROGRAM_ID = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
SYSTEM_RENT_PROGRAM_ID = Pubkey.from_string("SysvarRent111111111111111111111111111111111")

# Instruction discriminator for buy_exact_in (from IDL)
BUY_EXACT_IN_DISCRIMINATOR = bytes([250, 234, 13, 123, 213, 156, 19, 236])

# Compute budget settings
COMPUTE_UNIT_LIMIT = 87_000
COMPUTE_UNIT_PRICE = 1_000 

LAMPORTS_PER_SOL = 1_000_000_000


def derive_authority_pda() -> Pubkey:
    """
    Derive the authority PDA for the Raydium LaunchLab program.
    
    This PDA acts as the authority for pool vault operations and is generated
    using the AUTH_SEED as specified in the IDL.
    
    Returns:
        Pubkey: The derived authority PDA
    """
    AUTH_SEED = b"vault_auth_seed"  # From IDL PDA seeds
    authority_pda, _ = Pubkey.find_program_address(
        [AUTH_SEED],
        RAYDIUM_LAUNCHLAB_PROGRAM_ID
    )
    return authority_pda


def derive_event_authority_pda() -> Pubkey:
    """
    Derive the event authority PDA for the Raydium LaunchLab program.
    
    This PDA is used for emitting program events during swaps.
    
    Returns:
        Pubkey: The derived event authority PDA
    """
    EVENT_AUTHORITY_SEED = b"__event_authority"  # From IDL PDA seeds
    event_authority_pda, _ = Pubkey.find_program_address(
        [EVENT_AUTHORITY_SEED],
        RAYDIUM_LAUNCHLAB_PROGRAM_ID
    )
    return event_authority_pda


async def derive_pool_state_for_token(base_token_mint: Pubkey) -> Optional[Pubkey]:
    """
    Derive the pool state account for a given base token mint.
    
    Args:
        base_token_mint: The token mint address to search for
        
    Returns:
        Pubkey of the pool state account, or None if not found
    """
    seeds = [b"pool", bytes(base_token_mint), bytes(WSOL_MINT)]
    pool_state_pda, _ = Pubkey.find_program_address(seeds, RAYDIUM_LAUNCHLAB_PROGRAM_ID)
    return pool_state_pda

async def get_pool_accounts(client: AsyncClient, pool_state: Pubkey) -> dict:
    """
    Get the vault accounts for a pool from its pool state account.
    
    Searches for the vault addresses in the binary data.
    
    Args:
        client: Solana RPC client
        pool_state: The pool state account address
        
    Returns:
        Dictionary containing base_vault and quote_vault addresses
    """
    try:
        account_info = await client.get_account_info(pool_state)
        if not account_info.value:
            raise ValueError("Pool state account not found")
        
        base_vault_offset = 269
        quote_vault_offset = 301
        data = account_info.value.data
        
        base_vault = Pubkey(data[base_vault_offset:base_vault_offset + 32])
        quote_vault = Pubkey(data[quote_vault_offset:quote_vault_offset + 32])
        
        return {
            "base_vault": base_vault,
            "quote_vault": quote_vault
        }
    except Exception as e:
        print(f"Error getting pool accounts: {e}")
        return None


def get_associated_token_address(owner: Pubkey, mint: Pubkey) -> Pubkey:
    """
    Calculate the associated token account address for a given owner and mint.
    
    This manually implements the ATA derivation without requiring the spl-token package.
    
    Args:
        owner: The wallet that owns the token account
        mint: The token mint address
        
    Returns:
        Pubkey of the associated token account
    """
    ata_address, _ = Pubkey.find_program_address(
        [
            bytes(owner),
            bytes(TOKEN_PROGRAM_ID),
            bytes(mint)
        ],
        ASSOCIATED_TOKEN_PROGRAM_ID
    )
    return ata_address


def create_associated_token_account_idempotent_instruction(payer: Pubkey, owner: Pubkey, mint: Pubkey) -> Instruction:
    """
    Create an idempotent instruction to create an Associated Token Account.
    
    This uses the CreateIdempotent instruction which doesn't fail if the ATA already exists.
    
    Args:
        payer: The account that will pay for the creation
        owner: The owner of the new token account
        mint: The token mint
        
    Returns:
        Instruction for creating the ATA idempotently
    """
    ata_address = get_associated_token_address(owner, mint)
    
    accounts = [
        AccountMeta(pubkey=payer, is_signer=True, is_writable=True),           # Funding account
        AccountMeta(pubkey=ata_address, is_signer=False, is_writable=True),    # Associated token account
        AccountMeta(pubkey=owner, is_signer=False, is_writable=False),         # Wallet address
        AccountMeta(pubkey=mint, is_signer=False, is_writable=False),          # Token mint
        AccountMeta(pubkey=SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),  # System program
        AccountMeta(pubkey=TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),   # Token program
    ]
    
    data = bytes([1])
    
    return Instruction(
        program_id=ASSOCIATED_TOKEN_PROGRAM_ID,
        data=data,
        accounts=accounts
    )


def create_initialize_account_instruction(account: Pubkey, mint: Pubkey, owner: Pubkey) -> Instruction:
    """
    Create an InitializeAccount instruction for the Token Program.
    
    Args:
        account: The account to initialize
        mint: The token mint
        owner: The account owner
        
    Returns:
        Instruction for initializing the account
    """
    accounts = [
        AccountMeta(pubkey=account, is_signer=False, is_writable=True),
        AccountMeta(pubkey=mint, is_signer=False, is_writable=False),
        AccountMeta(pubkey=owner, is_signer=False, is_writable=False),
        AccountMeta(pubkey=SYSTEM_RENT_PROGRAM_ID, is_signer=False, is_writable=False),
    ]
    
    # InitializeAccount instruction discriminator (instruction 1 in Token Program)
    data = bytes([1])
    
    return Instruction(
        program_id=TOKEN_PROGRAM_ID,
        data=data,
        accounts=accounts
    )


def create_close_account_instruction(account: Pubkey, destination: Pubkey, owner: Pubkey) -> Instruction:
    """
    Create a CloseAccount instruction for the Token Program.
    
    Args:
        account: The account to close
        destination: Where to send the remaining lamports
        owner: The account owner (must sign)
        
    Returns:
        Instruction for closing the account
    """
    accounts = [
        AccountMeta(pubkey=account, is_signer=False, is_writable=True),
        AccountMeta(pubkey=destination, is_signer=False, is_writable=True),
        AccountMeta(pubkey=owner, is_signer=True, is_writable=False),
    ]
    
    data = bytes([9])
    
    return Instruction(
        program_id=TOKEN_PROGRAM_ID,
        data=data,
        accounts=accounts
    )


def create_wsol_account_with_seed(payer: Pubkey, seed: str, lamports: int) -> tuple[Pubkey, Instruction, Instruction]:
    """
    Create a WSOL account using createAccountWithSeed and initialize it.
    
    This replicates the exact pattern from the Solscan example where a new account
    is created with a seed and then initialized as a token account.
    
    Args:
        payer: The account that will pay for and own the new account
        seed: String seed for deterministic account generation
        lamports: Amount of lamports to transfer to the new account
        
    Returns:
        Tuple of (new_account_pubkey, create_instruction, initialize_instruction)
    """
    new_account = Pubkey.create_with_seed(payer, seed, TOKEN_PROGRAM_ID)

    create_ix = create_account_with_seed(
        CreateAccountWithSeedParams(
            from_pubkey=payer,
            to_pubkey=new_account,
            base=payer,
            seed=seed,
            lamports=lamports,
            space=165,  # Size of a token account
            owner=TOKEN_PROGRAM_ID
        )
    )
    
    initialize_ix = create_initialize_account_instruction(new_account, WSOL_MINT, payer)
    
    return new_account, create_ix, initialize_ix


def get_user_base_token_account(payer: Pubkey, base_mint: Pubkey) -> Pubkey:
    """
    Get the user's associated token account for the base token.
    
    In a real implementation, this should check if the account exists and create it if needed.
    For this example, we'll derive the standard ATA address.
    
    Args:
        payer: The user's wallet address
        base_mint: The base token mint address
        
    Returns:
        Pubkey of the user's base token account
    """
    return get_associated_token_address(payer, base_mint)


async def calculate_minimum_amount_out(
    client: AsyncClient,
    pool_state: Pubkey,
    amount_in: int,
    slippage_tolerance: float
) -> int:
    """
    Calculate the minimum amount out based on current pool state and slippage tolerance.
    
    This is a simplified calculation. In production, you should implement proper
    bonding curve calculations based on the pool's current state.
    
    Args:
        client: Solana RPC client
        pool_state: The pool state account
        amount_in: Amount of quote tokens being swapped in (in lamports)
        slippage_tolerance: Slippage tolerance as a decimal (0.25 = 25%)
        
    Returns:
        Minimum amount of base tokens to receive
    """
    try:
        # This is a simplified approach. In production, you should:
        # 1. Read the pool state data to get current reserves
        # 2. Calculate the expected output using the bonding curve formula
        # 3. Apply slippage tolerance
        
        # For now, we'll use a rough estimation based on the example transaction
        # From Solscan: 0.99 SOL → 16,057,173.389899 tokens
        estimated_tokens_per_sol = 16_057_173_389_899 / 0.99  # tokens per SOL
        sol_amount = amount_in / LAMPORTS_PER_SOL
        expected_output = int(sol_amount * estimated_tokens_per_sol)
        minimum_with_slippage = int(expected_output * (1 - slippage_tolerance))
        
        print(f"Estimated output: {expected_output:,} tokens")
        print(f"Minimum with {slippage_tolerance*100}% slippage: {minimum_with_slippage:,} tokens")
        
        return minimum_with_slippage
    except Exception as e:
        print(f"Error calculating minimum amount out: {e}")
        # Very conservative fallback
        return int(amount_in * 1000)


async def buy_exact_in(
    client: AsyncClient,
    base_token_mint: Pubkey,
    amount_in_sol: float,
    slippage_tolerance: float
) -> Optional[str]:
    """
    Execute a buy_exact_in transaction on Raydium LaunchLab.
    
    This function implements the exact transaction flow from the Solscan example:
    1. SetComputeUnitPrice
    2. SetComputeUnitLimit
    3. Create Associated Token Account for base token (idempotent)
    4. Create WSOL account with seed
    5. Initialize WSOL account
    6. Execute buy_exact_in instruction
    7. Close WSOL account
    
    Args:
        client: Solana RPC client
        base_token_mint: Address of the token to buy
        amount_in_sol: Amount of SOL to spend
        slippage_tolerance: Slippage tolerance as decimal
        
    Returns:
        Transaction signature if successful, None otherwise
    """
    try:
        print(f"Finding pool state for token: {base_token_mint}")
        pool_state = await derive_pool_state_for_token(base_token_mint)
        if not pool_state:
            print("Pool state not found for this token")
            return None
            
        pool_accounts = await get_pool_accounts(client, pool_state)
        base_vault = pool_accounts["base_vault"]
        quote_vault = pool_accounts["quote_vault"]
        
        print(f"Found pool state: {pool_state}")
        print(f"Base vault: {base_vault}")
        print(f"Quote vault: {quote_vault}")
        
        # Derive necessary PDAs
        authority = derive_authority_pda()
        event_authority = derive_event_authority_pda()
        
        # Calculate amounts
        amount_in = int(amount_in_sol * LAMPORTS_PER_SOL)
        minimum_amount_out = await calculate_minimum_amount_out(
            client, pool_state, amount_in, slippage_tolerance
        )
        
        print(f"Amount in: {amount_in} lamports ({amount_in_sol} SOL)")
        print(f"Minimum amount out: {minimum_amount_out}")
        
        # Step 1: Create Associated Token Account for base token (idempotent)
        user_base_token = get_associated_token_address(PAYER.pubkey(), base_token_mint)
        create_ata_ix = create_associated_token_account_idempotent_instruction(
            PAYER.pubkey(),
            PAYER.pubkey(),
            base_token_mint
        )
        
        # Step 2: Create WSOL account with seed
        import hashlib
        import time
        # Generate a unique seed based on timestamp and user pubkey
        seed_data = f"{int(time.time())}{str(PAYER.pubkey())}"
        wsol_seed = hashlib.sha256(seed_data.encode()).hexdigest()[:32]
        
        # Calculate required lamports (amount + small buffer for account creation)
        account_creation_lamports = 2_039_280  # Standard account creation cost
        total_lamports = amount_in + account_creation_lamports
        
        user_quote_token, create_wsol_ix, init_wsol_ix = create_wsol_account_with_seed(
            PAYER.pubkey(),
            wsol_seed,
            total_lamports
        )
        
        print(f"User base token account: {user_base_token}")
        print(f"User quote token account: {user_quote_token}")
        
        # Step 3: Build the buy_exact_in instruction
        accounts = [
            AccountMeta(pubkey=PAYER.pubkey(), is_signer=True, is_writable=True),          # payer
            AccountMeta(pubkey=authority, is_signer=False, is_writable=False),             # authority
            AccountMeta(pubkey=GLOBAL_CONFIG, is_signer=False, is_writable=False),         # global_config
            AccountMeta(pubkey=LETSBONK_PLATFORM_CONFIG, is_signer=False, is_writable=False),  # platform_config
            AccountMeta(pubkey=pool_state, is_signer=False, is_writable=True),             # pool_state
            AccountMeta(pubkey=user_base_token, is_signer=False, is_writable=True),        # user_base_token
            AccountMeta(pubkey=user_quote_token, is_signer=False, is_writable=True),       # user_quote_token
            AccountMeta(pubkey=base_vault, is_signer=False, is_writable=True),             # base_vault
            AccountMeta(pubkey=quote_vault, is_signer=False, is_writable=True),            # quote_vault
            AccountMeta(pubkey=base_token_mint, is_signer=False, is_writable=False),       # base_token_mint
            AccountMeta(pubkey=WSOL_MINT, is_signer=False, is_writable=False),             # quote_token_mint
            AccountMeta(pubkey=TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),      # base_token_program
            AccountMeta(pubkey=TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),      # quote_token_program
            AccountMeta(pubkey=event_authority, is_signer=False, is_writable=False),       # event_authority
            AccountMeta(pubkey=RAYDIUM_LAUNCHLAB_PROGRAM_ID, is_signer=False, is_writable=False),  # program
        ]
        
        # Instruction data: discriminator + amount_in + minimum_amount_out + share_fee_rate
        instruction_data = (
            BUY_EXACT_IN_DISCRIMINATOR +
            struct.pack("<Q", amount_in) +           # amount_in (u64)
            struct.pack("<Q", minimum_amount_out) +  # minimum_amount_out (u64)
            struct.pack("<Q", SHARE_FEE_RATE)        # share_fee_rate (u64): 0
        )
        
        buy_exact_in_ix = Instruction(
            program_id=RAYDIUM_LAUNCHLAB_PROGRAM_ID,
            data=instruction_data,
            accounts=accounts
        )
        
        # Step 4: Create close WSOL account instruction
        close_wsol_ix = create_close_account_instruction(user_quote_token, PAYER.pubkey(), PAYER.pubkey())
        
        # Step 5: Build complete transaction
        instructions = [
            set_compute_unit_price(COMPUTE_UNIT_PRICE),
            set_compute_unit_limit(COMPUTE_UNIT_LIMIT),
            # Instruction #3: Create Associated Token Account for base token (idempotent)
            create_ata_ix,
            # Instruction #4: Create WSOL account with seed
            create_wsol_ix,
            # Instruction #5: Initialize WSOL account
            init_wsol_ix,
            # Instruction #6: Execute buy_exact_in
            buy_exact_in_ix,
            # Instruction #7: Close WSOL account
            close_wsol_ix
        ]
        
        blockhash_resp = await client.get_latest_blockhash()
        recent_blockhash = blockhash_resp.value.blockhash
        
        message = Message.new_with_blockhash(
            instructions,
            PAYER.pubkey(),
            recent_blockhash
        )
        
        transaction = VersionedTransaction(message, [PAYER])
        
        print("Simulating transaction...")
        simulation = await client.simulate_transaction(transaction)
        
        if simulation.value.err:
            print(f"Simulation failed: {simulation.value.err}")
            return None
        
        print(f"Simulation successful. Compute units consumed: {simulation.value.units_consumed}")

        print("Sending transaction...")
        result = await client.send_transaction(
            transaction,
            opts=TxOpts(skip_preflight=True, preflight_commitment=Confirmed)
        )
        
        tx_signature = result.value
        print(f"Transaction sent: https://solscan.io/tx/{tx_signature}")
        
        print("Waiting for confirmation...")
        await client.confirm_transaction(tx_signature, commitment="confirmed")
        print("Transaction confirmed!")
        
        return tx_signature
        
    except Exception as e:
        print(f"Error executing buy_exact_in: {e}")
        import traceback
        traceback.print_exc()
        return None


async def main():
    """
    Main function to execute the buy_exact_in example.
    
    Takes configuration from environment variables or uses defaults.
    """
    try:
        print(f"Starting buy_exact_in for token: {TOKEN_MINT_ADDRESS}")
        print(f"Amount to spend: {SOL_AMOUNT_TO_SPEND} SOL")
        print(f"Slippage tolerance: {SLIPPAGE_TOLERANCE * 100}%")
        print(f"Using RPC endpoint: {RPC_ENDPOINT}")
        print()
        
        async with AsyncClient(RPC_ENDPOINT) as client:
            balance_resp = await client.get_balance(PAYER.pubkey())
            balance_sol = balance_resp.value / LAMPORTS_PER_SOL
            print(f"Wallet balance: {balance_sol:.6f} SOL")
            
            if balance_sol < SOL_AMOUNT_TO_SPEND + 0.001:  # Include some buffer for fees
                print("Insufficient SOL balance!")
                return
            
            tx_signature = await buy_exact_in(client, TOKEN_MINT_ADDRESS, SOL_AMOUNT_TO_SPEND, SLIPPAGE_TOLERANCE)
            
            if tx_signature:
                print(f"\n✅ Success! Transaction: {tx_signature}")
                print(f"🔗 View on Solscan: https://solscan.io/tx/{tx_signature}")
            else:
                print("\n❌ Transaction failed!")
                
    except ValueError as e:
        print(f"Invalid token mint address: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())