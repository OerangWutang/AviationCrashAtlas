/**
 * User repository.
 *
 * Synchronous SQLite queries via node:sqlite built-in.
 */

import type { DatabaseSync } from "node:sqlite";
import { getDb } from "./database.js";

/** Opaque DB handle type — passed through from tests to allow in-memory DBs. */
export type DbHandle = DatabaseSync;

export interface UserRow {
  id: string;
  email: string;
  password_hash: string;
  role: "analyst" | "reviewer" | "admin";
  atlas_user_id: string;
  encrypted_api_key: string;
  is_active: number;
  created_at: string;
  updated_at: string;
}

export interface SessionRow {
  id: string;
  user_id: string;
  created_at: string;
  expires_at: string;
  ip: string | null;
  user_agent: string | null;
}

// node:sqlite returns null-prototype objects; cast them explicitly
function toPlain<T>(obj: unknown): T | undefined {
  if (!obj) return undefined;
  return Object.assign({}, obj) as T;
}

export function findUserByEmail(email: string, db?: DatabaseSync): UserRow | undefined {
  const row = (db ?? getDb())
    .prepare("SELECT * FROM users WHERE email = ? AND is_active = 1")
    .get(email.toLowerCase().trim());
  return toPlain<UserRow>(row);
}

export function findUserById(id: string, db?: DatabaseSync): UserRow | undefined {
  const row = (db ?? getDb())
    .prepare("SELECT * FROM users WHERE id = ? AND is_active = 1")
    .get(id);
  return toPlain<UserRow>(row);
}

export interface CreateUserParams {
  id: string;
  email: string;
  passwordHash: string;
  role: "analyst" | "reviewer" | "admin";
  atlasUserId: string;
  encryptedApiKey: string;
}

export function createUser(params: CreateUserParams, db?: DatabaseSync): void {
  (db ?? getDb())
    .prepare(`
      INSERT INTO users (id, email, password_hash, role, atlas_user_id, encrypted_api_key)
      VALUES (@id, @email, @passwordHash, @role, @atlasUserId, @encryptedApiKey)
    `)
    .run({
      id: params.id,
      email: params.email.toLowerCase().trim(),
      passwordHash: params.passwordHash,
      role: params.role,
      atlasUserId: params.atlasUserId,
      encryptedApiKey: params.encryptedApiKey,
    });
}

export interface CreateSessionParams {
  id: string;
  userId: string;
  expiresAt: string;
  ip?: string;
  userAgent?: string;
}

export function createSession(params: CreateSessionParams, db?: DatabaseSync): void {
  (db ?? getDb())
    .prepare(`
      INSERT INTO sessions (id, user_id, expires_at, ip, user_agent)
      VALUES (@id, @userId, @expiresAt, @ip, @userAgent)
    `)
    .run({
      id: params.id,
      userId: params.userId,
      expiresAt: params.expiresAt,
      ip: params.ip ?? null,
      userAgent: params.userAgent ?? null,
    });
}

export function findSession(sessionId: string, db?: DatabaseSync): SessionRow | undefined {
  const now = new Date().toISOString();
  const row = (db ?? getDb())
    .prepare("SELECT * FROM sessions WHERE id = ? AND expires_at > ?")
    .get(sessionId, now);
  return toPlain<SessionRow>(row);
}

export function deleteSession(sessionId: string, db?: DatabaseSync): void {
  (db ?? getDb())
    .prepare("DELETE FROM sessions WHERE id = ?")
    .run(sessionId);
}

export function deleteUserSessions(userId: string, db?: DatabaseSync): void {
  (db ?? getDb())
    .prepare("DELETE FROM sessions WHERE user_id = ?")
    .run(userId);
}

export function sweepExpiredSessions(db?: DatabaseSync): void {
  const now = new Date().toISOString();
  (db ?? getDb())
    .prepare("DELETE FROM sessions WHERE expires_at <= ?")
    .run(now);
}
