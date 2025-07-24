import Client, { CommitmentLevel } from "@triton-one/yellowstone-grpc";
import { PublicKey } from "@solana/web3.js";
import { config } from "dotenv";

// Load environment variables
config();

const ENDPOINT = process.env.GEYSER_ENDPOINT!;
const TOKEN = process.env.GEYSER_TOKEN!;

// Program IDs
const TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";
const RAYDIUM_LAUNCHLAB_ID = "LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj";

async function listenToNewTokens() {
  try {
    // Connect to Geyser stream
    console.log("🔗 Connecting to Geyser stream...");
    const client = new Client(ENDPOINT, TOKEN, undefined);
    const stream = await client.subscribe();

    // Subscribe to transactions involving BOTH Token Program AND Raydium LaunchLab
    const request = {
      accounts: {},
      slots: {},
      transactions: {
        bonkTokens: {
          vote: false,
          failed: false,
          accountInclude: [TOKEN_PROGRAM_ID, RAYDIUM_LAUNCHLAB_ID],
          accountExclude: [],
          accountRequired: [],
        }
      },
      transactionsStatus: {},
      blocks: {},
      blocksMeta: {},
      entry: {},
      accountsDataSlice: [],
      commitment: CommitmentLevel.CONFIRMED
    };

    stream.write(request);
    console.log("🟢 Listening for new BONK.FUN token creation events...");

    // Process each transaction
    stream.on("data", (data) => {
      const tx = data?.transaction?.transaction;
      const meta = data?.transaction?.meta;

      if (!tx || !meta || meta.err !== null) return;

      const instructions = tx.message.instructions;
      const accountKeys = tx.message.accountKeys;
      
      let hasTokenProgram = false;
      let hasLaunchLab = false;
      let mintAddress = null;
      
      // Check if transaction involves both programs
      for (const instruction of instructions) {
        const programId = accountKeys[instruction.programIdIndex];
        const programIdBase58 = new PublicKey(programId).toString();
        
        if (programIdBase58 === TOKEN_PROGRAM_ID) {
          hasTokenProgram = true;
          // Check for InitializeMint instruction
          if (instruction.data[0] === 0) {
            mintAddress = new PublicKey(accountKeys[instruction.accounts[0]]).toString();
          }
        }
        
        if (programIdBase58 === RAYDIUM_LAUNCHLAB_ID) {
          hasLaunchLab = true;
        }
      }
      
      // Only process if both programs are involved (Bonk.fun launch)
      if (hasTokenProgram && hasLaunchLab && mintAddress) {
        console.log("🚀 NEW BONK.FUN TOKEN LAUNCHED!");
        console.log(`Slot: ${data.transaction.slot}`);
        console.log(`Signature: ${Buffer.from(tx.signatures[0]).toString("hex")}`);
        console.log(`Mint Address: ${mintAddress}`);
        console.log("=" + "=".repeat(50));
      }
    });

    stream.on("error", (err) => {
      console.error("❌ Stream error:", err);
      process.exit(1);
    });

    // Handle graceful shutdown
    process.on('SIGINT', () => {
      console.log("\n🛑 Shutting down...");
      stream.end();
      process.exit(0);
    });

  } catch (error) {
    console.error("❌ Failed to connect:", error);
    process.exit(1);
  }
}

// Start listening
listenToNewTokens();

/*
=== SETUP INSTRUCTIONS ===

1. Install dependencies:
   npm install @triton-one/yellowstone-grpc @solana/web3.js dotenv

2. Create .env file:
   GEYSER_ENDPOINT=your_geyser_endpoint_url
   GEYSER_TOKEN=your_geyser_api_token

3. Run:
   npx ts-node hello.ts
*/