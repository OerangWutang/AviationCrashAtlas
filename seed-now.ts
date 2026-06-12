/**
 * Standalone user seeder for the BFF. 
 * 
 * Usage:
 *   ATLAS_API_KEY="<valid-key>" node --experimental-sqlite seed-now.ts
 *
 * Reads sensitive values from environment variables so they're never
 * committed to source. Default passwords are for local dev only.
 */

import { randomUUID } from "node:crypto";
import argon2 from "argon2";
import { getConfig } from "./src/config.ts";
import { runMigrations } from "./src/db/database.ts";
import { createUser, findUserByEmail } from "./src/db/users.ts";
import { encryptApiKey } from "./src/auth/crypto.ts";

const email = "reviewer@atlas.local";
const role = "reviewer";
const atlasUserId = "07b2bb7a-6c11-4271-b1e2-f9a1c097c21c";

// Read secrets from environment — never hardcode real values
const password = process.env.ATLAS_SEED_PASSWORD ?? "reviewer123456";
const atlasRawApiKey = process.env.ATLAS_API_KEY;
if (!atlasRawApiKey) {
  console.error("ERROR: ATLAS_API_KEY environment variable is required.");
  console.error("Usage: ATLAS_API_KEY='<key>' node --experimental-sqlite seed-now.ts");
  process.exit(1);
}

const config = getConfig();
runMigrations();

const existing = findUserByEmail(email);
if (existing) {
  const { DatabaseSync } = await import("node:sqlite");
  const db = new DatabaseSync(config.dbPath);
  db.prepare("DELETE FROM users WHERE email = ?").run(email);
  db.close();
}

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