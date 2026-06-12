/**
 * Session management.
 */

import { nanoid } from "nanoid";
import { getConfig } from "../config.js";
import {
  createSession,
  findSession,
  deleteSession,
  findUserById,
  type DbHandle,
  type UserRow,
  type SessionRow,
} from "../db/users.js";

export interface SessionUser {
  id: string;
  email: string;
  role: "analyst" | "reviewer" | "admin";
  atlasUserId: string;
  encryptedApiKey: string;
}

function isBffPostgresEnabled(): boolean {
  return Boolean(process.env["BFF_DATABASE_URL"]);
}

function toSessionUser(user: UserRow): SessionUser {
  return {
    id: user.id,
    email: user.email,
    role: user.role,
    atlasUserId: user.atlas_user_id,
    encryptedApiKey: user.encrypted_api_key,
  };
}

function toPgSessionUser(row: {
  user_id: string;
  email: string;
  role: string;
  atlas_user_id: string;
  encrypted_api_key: string;
}): SessionUser {
  return {
    id: row.user_id,
    email: row.email,
    role: row.role as SessionUser["role"],
    atlasUserId: row.atlas_user_id,
    encryptedApiKey: row.encrypted_api_key,
  };
}

export async function createUserSession(
  userId: string,
  meta: { ip?: string; userAgent?: string },
  db?: DbHandle,
): Promise<string> {
  const config = getConfig();
  const sessionId = nanoid(32);
  const expiresAt = new Date(Date.now() + config.sessionTtlSeconds * 1000).toISOString();
  if (isBffPostgresEnabled()) {
    const { createSessionPg } = await import("../db/repos-pg.js");
    await createSessionPg({ id: sessionId, userId, expiresAt, ...meta });
  } else {
    createSession({ id: sessionId, userId, expiresAt, ...meta }, db);
  }
  return sessionId;
}

export async function resolveSession(sessionId: string, db?: DbHandle): Promise<SessionUser | null> {
  if (isBffPostgresEnabled()) {
    const { findSessionPg } = await import("../db/repos-pg.js");
    const row = await findSessionPg(sessionId);
    return row ? toPgSessionUser(row) : null;
  }

  const session: SessionRow | undefined = findSession(sessionId, db);
  if (!session) return null;
  const user: UserRow | undefined = findUserById(session.user_id, db);
  if (!user) return null;
  return toSessionUser(user);
}

export async function invalidateSession(sessionId: string, db?: DbHandle): Promise<void> {
  if (isBffPostgresEnabled()) {
    const { deleteSessionPg } = await import("../db/repos-pg.js");
    await deleteSessionPg(sessionId);
    return;
  }
  deleteSession(sessionId, db);
}

export function sessionCookieOptions(ttlSeconds: number, isProduction: boolean) {
  return {
    httpOnly: true,
    secure: isProduction,
    sameSite: "lax" as const,
    path: "/",
    maxAge: ttlSeconds,
  };
}

export function getCookieName(isProduction: boolean): string {
  const config = getConfig();
  if (!isProduction && config.sessionCookieName.startsWith("__Host-")) {
    return config.sessionCookieName.replace("__Host-", "");
  }
  return config.sessionCookieName;
}
