/**
 * PostgreSQL repository for users and sessions.
 *
 * Mirrors the API of src/db/users.ts but backed by PG instead of SQLite.
 * Used when BFF_DATABASE_URL is set.
 */

import pg from "pg";
import { getPgPool } from "./database-pg.js";

export interface UserRow {
  id: string;
  email: string;
  password_hash: string;
  role: string;
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

export interface SessionUser {
  id: string;
  email: string;
  role: string;
  atlasUserId: string;
  encryptedApiKey: string;
}

// ── Users ────────────────────────────────────────────────────────────────────

export async function findUserByEmailPg(email: string, pool?: pg.Pool): Promise<UserRow | null> {
  const p = pool ?? getPgPool();
  const result = await p.query("SELECT * FROM users WHERE email = $1", [email]);
  return result.rows[0] ?? null;
}

export async function findUserByIdPg(id: string, pool?: pg.Pool): Promise<UserRow | null> {
  const p = pool ?? getPgPool();
  const result = await p.query("SELECT * FROM users WHERE id = $1", [id]);
  return result.rows[0] ?? null;
}

export async function createUserPg(user: {
  id: string;
  email: string;
  passwordHash: string;
  role: string;
  atlasUserId: string;
  encryptedApiKey: string;
}, pool?: pg.Pool): Promise<void> {
  const p = pool ?? getPgPool();
  await p.query(
    `INSERT INTO users (id, email, password_hash, role, atlas_user_id, encrypted_api_key)
     VALUES ($1, $2, $3, $4, $5, $6)`,
    [user.id, user.email, user.passwordHash, user.role, user.atlasUserId, user.encryptedApiKey],
  );
}

// ── Sessions ─────────────────────────────────────────────────────────────────

export async function createSessionPg(session: {
  id: string;
  userId: string;
  expiresAt: string;
  ip?: string;
  userAgent?: string;
}, pool?: pg.Pool): Promise<void> {
  const p = pool ?? getPgPool();
  await p.query(
    `INSERT INTO sessions (id, user_id, expires_at, ip, user_agent)
     VALUES ($1, $2, $3, $4, $5)`,
    [session.id, session.userId, session.expiresAt, session.ip ?? null, session.userAgent ?? null],
  );
}

export async function findSessionPg(id: string, pool?: pg.Pool): Promise<(SessionRow & UserRow) | null> {
  const p = pool ?? getPgPool();
  const result = await p.query(
    `SELECT s.*, u.email, u.role, u.atlas_user_id, u.encrypted_api_key, u.is_active
     FROM sessions s JOIN users u ON u.id = s.user_id
     WHERE s.id = $1 AND s.expires_at > NOW() AND u.is_active = 1`,
    [id],
  );
  return result.rows[0] ?? null;
}

export async function deleteSessionPg(id: string, pool?: pg.Pool): Promise<void> {
  const p = pool ?? getPgPool();
  await p.query("DELETE FROM sessions WHERE id = $1", [id]);
}

export async function deleteUserSessionsPg(userId: string, pool?: pg.Pool): Promise<void> {
  const p = pool ?? getPgPool();
  await p.query("DELETE FROM sessions WHERE user_id = $1", [userId]);
}

export async function sweepExpiredSessionsPg(pool?: pg.Pool): Promise<void> {
  const p = pool ?? getPgPool();
  await p.query("DELETE FROM sessions WHERE expires_at <= NOW()");
}

// ── Leads ────────────────────────────────────────────────────────────────────

export async function createLeadPg(lead: {
  id: string;
  name: string;
  email: string;
  organization: string;
  roleType: string;
  organizationType: string;
  useCase: string;
  message?: string;
}, pool?: pg.Pool): Promise<void> {
  const p = pool ?? getPgPool();
  await p.query(
    `INSERT INTO request_access_leads (id, name, email, organization, role_type, organization_type, use_case, message)
     VALUES ($1, $2, $3, $4, $5, $6, $7, $8)`,
    [lead.id, lead.name, lead.email, lead.organization, lead.roleType, lead.organizationType, lead.useCase, lead.message ?? null],
  );
}

export async function listLeadsPg(limit: number = 200, pool?: pg.Pool): Promise<any[]> {
  const p = pool ?? getPgPool();
  const result = await p.query(
    "SELECT * FROM request_access_leads ORDER BY created_at DESC LIMIT $1",
    [limit],
  );
  return result.rows;
}

export async function countLeadsPg(pool?: pg.Pool): Promise<number> {
  const p = pool ?? getPgPool();
  const result = await p.query("SELECT COUNT(*) AS count FROM request_access_leads");
  return Number(result.rows[0].count);
}