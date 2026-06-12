/**
 * Symmetric encryption for Atlas API keys stored in the BFF database.
 *
 * Each user's Atlas API key is stored AES-256-GCM encrypted. The encryption
 * key is derived from BFF_SESSION_SECRET (or a dedicated BFF_ENCRYPTION_KEY
 * if provided). The raw key NEVER appears in logs, responses, or client JS.
 *
 * Format of the stored ciphertext:
 *   base64(iv[12 bytes] + ciphertext + authTag[16 bytes])
 *
 * All operations are synchronous (Node crypto is sync-capable and this is
 * called at login time, not per-request in the hot path).
 */

import { createCipheriv, createDecipheriv, randomBytes, scryptSync } from "node:crypto";

const ALGORITHM = "aes-256-gcm";
const IV_LENGTH = 12;
const AUTH_TAG_LENGTH = 16;
const KEY_LENGTH = 32; // 256 bits

function deriveKey(secret: string): Buffer {
  // scrypt with a fixed salt derived from the secret name. The salt is not
  // secret — it only prevents pre-computation across different deployments.
  const salt = Buffer.from("atlas-bff-apikey-encryption-v1", "utf8");
  return scryptSync(secret, salt, KEY_LENGTH);
}

/**
 * Encrypt a plaintext Atlas API key.
 * Returns a base64-encoded string safe to store in SQLite.
 */
export function encryptApiKey(apiKey: string, secret: string): string {
  const key = deriveKey(secret);
  const iv = randomBytes(IV_LENGTH);
  const cipher = createCipheriv(ALGORITHM, key, iv);
  const encrypted = Buffer.concat([cipher.update(apiKey, "utf8"), cipher.final()]);
  const authTag = cipher.getAuthTag();
  // Pack: iv || ciphertext || authTag
  const packed = Buffer.concat([iv, encrypted, authTag]);
  return packed.toString("base64");
}

/**
 * Decrypt a stored Atlas API key.
 * Throws if the ciphertext is tampered with (auth tag mismatch).
 */
export function decryptApiKey(stored: string, secret: string): string {
  const key = deriveKey(secret);
  const packed = Buffer.from(stored, "base64");

  const iv = packed.subarray(0, IV_LENGTH);
  const authTag = packed.subarray(packed.length - AUTH_TAG_LENGTH);
  const ciphertext = packed.subarray(IV_LENGTH, packed.length - AUTH_TAG_LENGTH);

  const decipher = createDecipheriv(ALGORITHM, key, iv);
  decipher.setAuthTag(authTag);
  const decrypted = Buffer.concat([decipher.update(ciphertext), decipher.final()]);
  return decrypted.toString("utf8");
}
