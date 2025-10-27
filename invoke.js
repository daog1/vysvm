#!/usr/bin/env node
// Script to invoke Solana program
// Run: node invoke.js <PROGRAM_ID>

const {
  Connection,
  Keypair,
  PublicKey,
  Transaction,
  TransactionInstruction,
  sendAndConfirmTransaction,
} = require("@solana/web3.js");
const fs = require("fs");

async function invoke() {
  const programId = new PublicKey(process.argv[2]);

  const connection = new Connection("http://localhost:8899", "confirmed");

  const keypairPath = `${process.env.HOME}/.config/solana/id.json`;
  const keypair = Keypair.fromSecretKey(
    new Uint8Array(JSON.parse(fs.readFileSync(keypairPath, "utf-8"))),
  );

  console.log("Invoking program:", programId.toBase58());
  console.log("From wallet:", keypair.publicKey.toBase58());

  // Create instruction with no accounts, no data
  const instruction = new TransactionInstruction({
    keys: [
      {
        pubkey: keypair.publicKey,
        isSigner: true,
        isWritable: false,
      },
    ],
    programId,
    data: Buffer.alloc(0),
  });

  const tx = new Transaction().add(instruction);

  try {
    const signature = await sendAndConfirmTransaction(
      connection,
      tx,
      [keypair],
      { commitment: "confirmed" },
    );

    console.log("\n✓ Transaction confirmed!");
    console.log("Signature:", signature);

    // Fetch and display logs
    const txDetails = await connection.getTransaction(signature, {
      commitment: "confirmed",
      maxSupportedTransactionVersion: 0,
    });

    console.log("\nProgram logs:");
    txDetails?.meta?.logMessages?.forEach((log) => {
      if (log.includes("Program log:")) {
        console.log("  →", log);
      } else {
        console.log("   ", log);
      }
    });
  } catch (err) {
    console.error("Error:", err.message);
    process.exit(1);
  }
}

invoke();
