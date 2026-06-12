#!/usr/bin/env node
/**
 * Seed the BFF with an initial user.
 *
 * Usage:
 *   BFF_DB_PATH=./bff.db \
 *   BFF_SESSION_SECRET=your-secret \
 *   ATLAS_RAW_API_KEY=atlas_live_... \
 *   ATLAS_USER_ID=<uuid-from-atlas-bootstrap> \
 *   node --loader tsx src/scripts/seed-user.ts \
 *     --email admin@yourfirm.com \
 *     --role reviewer
 *
 * The ATLAS_RAW_API_KEY is the key printed by `atlas bootstrap --role reviewer`.
 * The ATLAS_USER_ID is the user_id UUID baked into that key (from ApiKeyModel).
 *
 * This script stores the key encrypted at rest. It never appears in logs.
 */

import { randomUUID } from "node:crypto";
import argon2 from "argon2";
import { getConfig } from "../config.js";
import { runMigrations } from "../db/database.js";
import { createUser, findUserByEmail } from "../db/users.js";
import { encryptApiKey } from "../auth/crypto.js";
import { createInterface } from "node:readline";

async function prompt(question: string, hidden = false): Promise<string> {
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  return new Promise((resolve) => {
    if (hidden) {
      process.stdout.write(question);
      process.stdin.setRawMode?.(true);
      let input = "";
      process.stdin.on("data", (chunk: Buffer) => {
        const char = chunk.toString();
        if (char === "\r" || char === "\n") {
          process.stdin.setRawMode?.(false);
          process.stdout.write("\n");
          rl.close();
          resolve(input);
        } else if (char === "\u0003") {
          process.exit(1);
        } else {
          input += char;
          process.stdout.write("*");
        }
      });
    } else {
      rl.question(question, (answer) => {
        rl.close();
        resolve(answer);
      });
    }
  });
}

function parseArgs(): { email?: string; role?: string } {
  const args = process.argv.slice(2);
  const result: { email?: string; role?: string } = {};
  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--email" && args[i + 1]) result.email = args[++i];
    if (args[i] === "--role" && args[i + 1]) result.role = args[++i];
  }
  return result;
}

async function main() {
  const config = getConfig();
  runMigrations();

  const args = parseArgs();

  const email = args.email ?? (await prompt("Email: "));
  const roleInput = args.role ?? (await prompt("Role (analyst/reviewer/admin): "));
  const role = roleInput as "analyst" | "reviewer" | "admin";

  if (!["analyst", "reviewer", "admin"].includes(role)) {
    console.error("Role must be analyst, reviewer, or admin");
    process.exit(1);
  }

  const existing = findUserByEmail(email);
  if (existing) {
    console.error(`User with email ${email} already exists`);
    process.exit(1);
  }

  const password = await prompt("Password: ", true);
  const confirmPassword = await prompt("Confirm password: ", true);
  if (password !== confirmPassword) {
    console.error("Passwords do not match");
    process.exit(1);
  }
  if (password.length < 12) {
    console.error("Password must be at least 12 characters");
    process.exit(1);
  }

  const atlasRawApiKey =
    process.env["ATLAS_RAW_API_KEY"] ?? (await prompt("Atlas API key (from atlas bootstrap): "));
  const atlasUserId =
    process.env["ATLAS_USER_ID"] ?? (await prompt("Atlas user_id UUID (from ApiKeyModel): "));

  console.log("\nCreating user...");

  const passwordHash = await argon2.hash(password, {
    type: argon2.argon2id,
    memoryCost: 65536,
    timeCost: 3,
    parallelism: 4,
  });

  const encryptedApiKey = encryptApiKey(atlasRawApiKey, config.sessionSecret);

  createUser({
    id: randomUUID(),
    email,
    passwordHash,
    role,
    atlasUserId,
    encryptedApiKey,
  });

  console.log(`✓ User created: ${email} (${role})`);
  console.log("  The Atlas API key has been encrypted and stored.");
  console.log("  The raw key is no longer needed.");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
