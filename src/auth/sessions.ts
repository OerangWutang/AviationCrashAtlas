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

export function createUserSession(
  userId: string,
  meta: { ip?: string; userAgent?: string },
  db?: DbHandle,
): string {
  const config = getConfig();
  const sessionId = nanoid(32);
  const expiresAt = new Date(Date.now() + config.sessionTtlSeconds * 1000).toISOString();
  createSession({ id: sessionId, userId, expiresAt, ...meta }, db);
  return sessionId;
}

export function resolveSession(sessionId: string, db?: DbHandle): SessionUser | null {
  const session: SessionRow | undefined = findSession(sessionId, db);
  if (!session) return null;
  const user: UserRow | undefined = findUserById(session.user_id, db);
  if (!user) return null;
  return {
    id: user.id,
    email: user.email,
    role: user.role,
    atlasUserId: user.atlas_user_id,
    encryptedApiKey: user.encrypted_api_key,
  };
}

export function invalidateSession(sessionId: string, db?: DbHandle): void {
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
