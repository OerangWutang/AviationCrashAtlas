/**
 * Auth routes: POST /auth/login, GET /auth/me, POST /auth/logout
 *
 * These are the only routes that interact with the human identity layer.
 * FastAPI never sees these — they live entirely in the BFF.
 *
 * Security properties:
 *   - POST /auth/login:  Verifies Argon2 password, sets HttpOnly session cookie,
 *                        sets CSRF cookie. Rate-limited per IP.
 *   - GET  /auth/me:     Resolves session cookie → user identity.
 *                        Issues/refreshes CSRF cookie. No API key forwarded.
 *   - POST /auth/logout: Invalidates session server-side, clears cookies.
 *                        Requires CSRF header (it's a mutation).
 */

import type { FastifyInstance, FastifyRequest, FastifyReply } from "fastify";
import argon2 from "argon2";
import { getConfig } from "../config.js";
import { findUserByEmail, type UserRow } from "../db/users.js";
import {
  createUserSession,
  resolveSession,
  invalidateSession,
  sessionCookieOptions,
  getCookieName,
  type SessionUser,
} from "./sessions.js";
import {
  generateCsrfToken,
  verifyCsrfToken,
  csrfCookieOptions,
  CSRF_COOKIE,
  CSRF_HEADER,
} from "../middleware/csrf.js";
import {
  checkLoginRateLimit,
  recordFailedLogin,
} from "../middleware/rateLimit.js";

interface LoginBody {
  email: string;
  password: string;
}

const DUMMY_ARGON2ID_HASH = "$argon2id$v=19$m=65536,t=3,p=4$cGxhY2Vob2xkZXJfc2FsdF8xMjM0NQ$nkHFQliDUlMHfY+9SGsMcLiESfQcixfgWNgR6X5jZUw";

function isBffPostgresEnabled(): boolean {
  return Boolean(process.env["BFF_DATABASE_URL"]);
}

async function findActiveUserByEmail(email: string): Promise<UserRow | null> {
  if (isBffPostgresEnabled()) {
    const { findUserByEmailPg } = await import("../db/repos-pg.js");
    const user = await findUserByEmailPg(email.toLowerCase().trim());
    return user && Number(user.is_active) === 1 ? (user as UserRow) : null;
  }
  return findUserByEmail(email) ?? null;
}

export async function registerAuthRoutes(app: FastifyInstance): Promise<void> {
  const config = getConfig();

  // ── POST /auth/login ─────────────────────────────────────────────────────

  app.post(
    "/auth/login",
    async (req: FastifyRequest<{ Body: LoginBody }>, reply: FastifyReply) => {
      // Rate-limit by IP before doing anything else
      const ip = req.ip ?? "unknown";
      const { allowed, retryAfterMs } = checkLoginRateLimit(
        ip,
        config.loginRateLimit,
        config.loginRateLimitWindowMs,
      );
      if (!allowed) {
        return reply
          .status(429)
          .header("Retry-After", Math.ceil(retryAfterMs / 1000).toString())
          .send({ error: "Too many login attempts. Please try again later." });
      }

      const { email, password } = req.body ?? {};
      if (!email || !password) {
        return reply.status(400).send({ error: "email and password are required" });
      }

      // Always verify against a valid Argon2id hash.  Missing users use a
      // real precomputed dummy hash so the negative path does the same class of
      // work instead of failing quickly during hash parsing.
      const user = await findActiveUserByEmail(email);
      const hashToVerify = user?.password_hash ?? DUMMY_ARGON2ID_HASH;

      let valid = false;
      try {
        valid = await argon2.verify(hashToVerify, password);
      } catch {
        valid = false;
      }

      if (!valid || !user) {
        recordFailedLogin(ip, config.loginRateLimit, config.loginRateLimitWindowMs);
        // Uniform error message regardless of whether user exists
        return reply.status(401).send({ error: "Invalid email or password" });
      }

      // Create server-side session
      const sessionId = await createUserSession(user.id, {
        ip: req.ip,
        userAgent: req.headers["user-agent"],
      });

      // Issue CSRF token
      const csrfToken = generateCsrfToken();

      const cookieName = getCookieName(config.isProduction);
      const cookieOpts = sessionCookieOptions(config.sessionTtlSeconds, config.isProduction);

      reply
        .setCookie(cookieName, sessionId, cookieOpts)
        .setCookie(CSRF_COOKIE, csrfToken, csrfCookieOptions(config.isProduction))
        .status(200)
        .send({
          userId: user.atlas_user_id,
          email: user.email,
          displayName: user.email.split("@")[0] ?? user.email,
          role: user.role,
          tenantId: null,
          tenantRole: null,
          permissions: roleToPermissions(user.role),
        });
    },
  );

  // ── GET /auth/me ─────────────────────────────────────────────────────────

  app.get("/auth/me", async (req: FastifyRequest, reply: FastifyReply) => {
    const cookieName = getCookieName(config.isProduction);
    const sessionId = req.cookies[cookieName];

    if (!sessionId) {
      return reply.status(401).send({ error: "No session" });
    }

    const sessionUser = await resolveSession(sessionId);
    if (!sessionUser) {
      // Session expired or invalid — clear the stale cookie
      reply.clearCookie(cookieName, { path: "/" });
      return reply.status(401).send({ error: "Session expired" });
    }

    // Refresh CSRF token on /auth/me so the SPA can always get a fresh one
    const csrfToken = generateCsrfToken();
    reply.setCookie(CSRF_COOKIE, csrfToken, csrfCookieOptions(config.isProduction));

    return reply.send({
      userId: sessionUser.atlasUserId,
      email: sessionUser.email,
      displayName: sessionUser.email.split("@")[0] ?? sessionUser.email,
      role: sessionUser.role,
      tenantId: null,
      tenantRole: null,
      permissions: roleToPermissions(sessionUser.role),
    });
  });

  // ── POST /auth/logout ────────────────────────────────────────────────────

  app.post("/auth/logout", async (req: FastifyRequest, reply: FastifyReply) => {
    // Logout is a mutation — enforce CSRF
    const cookieName = getCookieName(config.isProduction);
    const sessionId = req.cookies[cookieName];

    if (sessionId) {
      const csrfHeader = req.headers[CSRF_HEADER] as string | undefined;
      const csrfCookie = req.cookies[CSRF_COOKIE];
      if (!verifyCsrfToken(csrfCookie, csrfHeader)) {
        // Invalid CSRF — still clear session for safety but return 403
        await invalidateSession(sessionId);
        reply.clearCookie(cookieName, { path: "/" }).clearCookie(CSRF_COOKIE, { path: "/" });
        return reply.status(403).send({ error: "CSRF validation failed" });
      }
      await invalidateSession(sessionId);
    }

    reply
      .clearCookie(cookieName, { path: "/" })
      .clearCookie(CSRF_COOKIE, { path: "/" })
      .status(204)
      .send();
  });
}

// ── Permission map (mirrors atlas.domain.enums.Role semantics) ───────────────

/**
 * Map role → UI permission flags that the frontend reads from /auth/me.
 * Real enforcement is always server-side; these flags only gate UI affordances.
 */
function roleToPermissions(role: string): Record<string, boolean> {
  const base = {
    canViewCases: true,
    canViewClaims: true,
    canViewConflicts: true,
    canViewProvenance: true,
    canViewTimeline: true,
    canViewReports: true,
    canViewAudit: true,
    canResolveConflicts: false,
    canReopenConflicts: false,
    canUploadDocuments: false,
    canIngestDocuments: false,
    canGenerateReports: false,
  };

  if (role === "reviewer" || role === "admin") {
    Object.assign(base, {
      canResolveConflicts: true,
      canReopenConflicts: true,
      canUploadDocuments: true,
      canIngestDocuments: true,
      canGenerateReports: true,
    });
  }

  return base;
}

// ── Session middleware helper (used by all protected BFF routes) ─────────────

/**
 * Resolve the session from the request and return the SessionUser.
 * Throws a Fastify 401 reply if no valid session is found.
 */
export async function requireSession(
  req: FastifyRequest,
  reply: FastifyReply,
): Promise<SessionUser | undefined> {
  const config = getConfig();
  const cookieName = getCookieName(config.isProduction);
  const sessionId = req.cookies[cookieName];

  if (!sessionId) {
    await reply.status(401).send({ error: "Authentication required" });
    // Returning undefined signals to the caller that the reply was sent
    return undefined;
  }

  const sessionUser = await resolveSession(sessionId);
  if (!sessionUser) {
    reply.clearCookie(cookieName, { path: "/" });
    await reply.status(401).send({ error: "Session expired" });
    return undefined;
  }

  return sessionUser;
}

/**
 * Verify CSRF for mutating requests. Returns false and sends 403 if invalid.
 */
export function checkCsrf(req: FastifyRequest, reply: FastifyReply): boolean {
  const csrfHeader = req.headers[CSRF_HEADER] as string | undefined;
  const csrfCookie = req.cookies[CSRF_COOKIE];
  if (!verifyCsrfToken(csrfCookie, csrfHeader)) {
    void reply.status(403).send({ error: "CSRF validation failed" });
    return false;
  }
  return true;
}
