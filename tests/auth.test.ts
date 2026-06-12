/**
 * BFF auth integration tests.
 *
 * Tests the complete auth flow end-to-end using an in-memory SQLite database
 * and a real Fastify app instance. No network calls to Atlas are made.
 *
 * Covers the acceptance criteria from the implementation plan:
 * 1. POST /auth/login with valid creds sets HttpOnly session cookie; invalid → 401
 * 2. GET /auth/me without session → 401
 * 3. GET /auth/me with valid session → role and email returned
 * 4. POST /auth/logout invalidates session; subsequent /auth/me → 401
 * 5. Rate limiting blocks after N failed attempts
 * 6. CSRF enforcement on /auth/logout
 */

import { randomUUID } from "node:crypto";
import argon2 from "argon2";
import { jest } from "@jest/globals";

// ── Test setup helpers ────────────────────────────────────────────────────────

// Use in-memory DB and minimal config for tests
process.env["BFF_DB_PATH"] = ":memory:";
process.env["BFF_SESSION_SECRET"] = "test-secret-that-is-long-enough-32chars";
process.env["NODE_ENV"] = "test";
process.env["ATLAS_SEED_API_KEY"] = "test-atlas-key";
process.env["WEB_ORIGIN"] = "http://localhost:5173";
process.env["ATLAS_API_BASE_URL"] = "http://localhost:8000";
process.env["LOGIN_RATE_LIMIT"] = "5";
process.env["LOGIN_RATE_LIMIT_WINDOW_MS"] = "60000";

import { _resetConfig, getConfig } from "../src/config.js";
import { _closeDb, runMigrations } from "../src/db/database.js";
import { createUser } from "../src/db/users.js";
import { encryptApiKey } from "../src/auth/crypto.js";
import { buildApp } from "../src/server.js";
import { _clearRateLimits } from "../src/middleware/rateLimit.js";
import { CSRF_COOKIE, CSRF_HEADER } from "../src/middleware/csrf.js";
import type { FastifyInstance } from "fastify";

const TEST_EMAIL = "reviewer@test.com";
const TEST_PASSWORD = "test-password-secure-123";
const TEST_ATLAS_USER_ID = randomUUID();
const TEST_ENCRYPTED_KEY = encryptApiKey(
  "test-raw-api-key",
  "test-secret-that-is-long-enough-32chars",
);

async function seedTestUser(): Promise<void> {
  const passwordHash = await argon2.hash(TEST_PASSWORD, {
    type: argon2.argon2id,
    memoryCost: 4096, // low cost for tests
    timeCost: 2,      // argon2 minimum is 2
    parallelism: 1,
  });

  createUser({
    id: randomUUID(),
    email: TEST_EMAIL,
    passwordHash,
    role: "reviewer",
    atlasUserId: TEST_ATLAS_USER_ID,
    encryptedApiKey: TEST_ENCRYPTED_KEY,
  });
}

// ── Cookie parser helper ─────────────────────────────────────────────────────

function parseCookies(headers: string[] | string | undefined): Record<string, string> {
  const result: Record<string, string> = {};
  const cookieHeaders = Array.isArray(headers) ? headers : headers ? [headers] : [];
  for (const h of cookieHeaders) {
    // Each set-cookie header: "name=value; options..."
    const parts = h.split(";")[0]?.split("=") ?? [];
    if (parts.length >= 2) {
      const name = parts[0]?.trim() ?? "";
      const value = parts.slice(1).join("=").trim();
      result[name] = value;
    }
  }
  return result;
}

function getCookieOption(header: string | undefined, option: string): boolean {
  if (!header) return false;
  return header.toLowerCase().includes(option.toLowerCase());
}

// ── Test suite ───────────────────────────────────────────────────────────────

describe("BFF Auth", () => {
  let app: FastifyInstance;

  beforeAll(async () => {
    _resetConfig();
    runMigrations();
    await seedTestUser();
    app = await buildApp();
  });

  afterAll(async () => {
    await app.close();
    _closeDb();
  });

  beforeEach(() => {
    _clearRateLimits();
  });

  // ── Login ──────────────────────────────────────────────────────────────────

  describe("POST /auth/login", () => {
    it("returns 200 and sets HttpOnly session cookie on valid credentials", async () => {
      const res = await app.inject({
        method: "POST",
        url: "/auth/login",
        payload: { email: TEST_EMAIL, password: TEST_PASSWORD },
      });

      expect(res.statusCode).toBe(200);
      const body = JSON.parse(res.body) as { email: string; role: string; userId: string; displayName: string };
      expect(body.email).toBe(TEST_EMAIL);
      expect(body.role).toBe("reviewer");
      expect(body.userId).toBe(TEST_ATLAS_USER_ID);
      expect(body.displayName).toBeTruthy();

      // Session cookie must be HttpOnly
      const setCookieHeaders = res.headers["set-cookie"];
      const cookies = parseCookies(setCookieHeaders);
      const sessionCookieName = Object.keys(cookies).find(
        (k) => k.includes("atlas-session") || k === "atlas-session",
      );
      expect(sessionCookieName).toBeDefined();

      // Verify HttpOnly flag
      const rawHeaders = Array.isArray(setCookieHeaders) ? setCookieHeaders : [setCookieHeaders ?? ""];
      const sessionHeader = rawHeaders.find((h) => h.includes("atlas-session"));
      expect(getCookieOption(sessionHeader, "HttpOnly")).toBe(true);

      // CSRF cookie must also be set (and NOT HttpOnly)
      const csrfHeader = rawHeaders.find((h) => h.startsWith(CSRF_COOKIE));
      expect(csrfHeader).toBeDefined();
      expect(getCookieOption(csrfHeader, "HttpOnly")).toBe(false);
    });

    it("returns 401 for invalid password", async () => {
      const res = await app.inject({
        method: "POST",
        url: "/auth/login",
        payload: { email: TEST_EMAIL, password: "wrong-password" },
      });

      expect(res.statusCode).toBe(401);
      expect(res.headers["set-cookie"]).toBeUndefined();
    });

    it("returns 401 for unknown email", async () => {
      const res = await app.inject({
        method: "POST",
        url: "/auth/login",
        payload: { email: "nobody@test.com", password: TEST_PASSWORD },
      });

      expect(res.statusCode).toBe(401);
    });

    it("returns 400 when body fields are missing", async () => {
      const res = await app.inject({
        method: "POST",
        url: "/auth/login",
        payload: { email: TEST_EMAIL },
      });

      expect(res.statusCode).toBe(400);
    });

    it("rate-limits after repeated failures", async () => {
      const limit = parseInt(process.env["LOGIN_RATE_LIMIT"] ?? "5", 10);

      // Exhaust the limit
      for (let i = 0; i < limit; i++) {
        await app.inject({
          method: "POST",
          url: "/auth/login",
          payload: { email: TEST_EMAIL, password: "wrong" },
        });
      }

      const res = await app.inject({
        method: "POST",
        url: "/auth/login",
        payload: { email: TEST_EMAIL, password: "wrong" },
      });

      expect(res.statusCode).toBe(429);
      expect(res.headers["retry-after"]).toBeDefined();
    });
  });

  // ── /auth/me ───────────────────────────────────────────────────────────────

  describe("GET /auth/me", () => {
    it("returns 401 when no session cookie is present", async () => {
      const res = await app.inject({ method: "GET", url: "/auth/me" });
      expect(res.statusCode).toBe(401);
    });

    it("returns user identity when session is valid", async () => {
      // Log in first
      const loginRes = await app.inject({
        method: "POST",
        url: "/auth/login",
        payload: { email: TEST_EMAIL, password: TEST_PASSWORD },
      });
      expect(loginRes.statusCode).toBe(200);

      // Extract session cookie
      const cookies = parseCookies(loginRes.headers["set-cookie"]);
      const sessionCookieName = Object.keys(cookies).find((k) =>
        k.includes("atlas-session"),
      );
      expect(sessionCookieName).toBeDefined();
      const sessionValue = cookies[sessionCookieName!];

      // Call /auth/me with session cookie
      const meRes = await app.inject({
        method: "GET",
        url: "/auth/me",
        cookies: { [sessionCookieName!]: sessionValue! },
      });

      expect(meRes.statusCode).toBe(200);
      const body = JSON.parse(meRes.body) as { email: string; role: string; permissions: Record<string, boolean> };
      expect(body.email).toBe(TEST_EMAIL);
      expect(body.role).toBe("reviewer");
      expect(body.permissions["can_resolve_conflicts"]).toBe(true);
      expect(body.permissions["can_view_cases"]).toBe(true);
    });

    it("returns 401 with an invalid/expired session id", async () => {
      const res = await app.inject({
        method: "GET",
        url: "/auth/me",
        cookies: { "atlas-session": "totally-invalid-session-id" },
      });

      expect(res.statusCode).toBe(401);
    });
  });

  // ── Logout ─────────────────────────────────────────────────────────────────

  describe("POST /auth/logout", () => {
    async function loginAndGetSessionAndCsrf(): Promise<{
      sessionCookieName: string;
      sessionValue: string;
      csrfToken: string;
    }> {
      const loginRes = await app.inject({
        method: "POST",
        url: "/auth/login",
        payload: { email: TEST_EMAIL, password: TEST_PASSWORD },
      });

      const cookies = parseCookies(loginRes.headers["set-cookie"]);
      const sessionCookieName =
        Object.keys(cookies).find((k) => k.includes("atlas-session")) ?? "atlas-session";
      const sessionValue = cookies[sessionCookieName] ?? "";
      const csrfToken = cookies[CSRF_COOKIE] ?? "";

      return { sessionCookieName, sessionValue, csrfToken };
    }

    it("invalidates session; subsequent /auth/me returns 401", async () => {
      const { sessionCookieName, sessionValue, csrfToken } =
        await loginAndGetSessionAndCsrf();

      // Logout with valid CSRF
      const logoutRes = await app.inject({
        method: "POST",
        url: "/auth/logout",
        cookies: {
          [sessionCookieName]: sessionValue,
          [CSRF_COOKIE]: csrfToken,
        },
        headers: { [CSRF_HEADER]: csrfToken },
      });

      expect(logoutRes.statusCode).toBe(204);

      // /auth/me should now return 401
      const meRes = await app.inject({
        method: "GET",
        url: "/auth/me",
        cookies: { [sessionCookieName]: sessionValue },
      });
      expect(meRes.statusCode).toBe(401);
    });

    it("returns 403 when CSRF header is missing", async () => {
      const { sessionCookieName, sessionValue, csrfToken } =
        await loginAndGetSessionAndCsrf();

      const res = await app.inject({
        method: "POST",
        url: "/auth/logout",
        cookies: {
          [sessionCookieName]: sessionValue,
          [CSRF_COOKIE]: csrfToken,
        },
        // Deliberately omit the X-CSRF-Token header
      });

      expect(res.statusCode).toBe(403);
    });

    it("returns 403 when CSRF header does not match cookie", async () => {
      const { sessionCookieName, sessionValue, csrfToken } =
        await loginAndGetSessionAndCsrf();

      const res = await app.inject({
        method: "POST",
        url: "/auth/logout",
        cookies: {
          [sessionCookieName]: sessionValue,
          [CSRF_COOKIE]: csrfToken,
        },
        headers: { [CSRF_HEADER]: "tampered-csrf-token" },
      });

      expect(res.statusCode).toBe(403);
    });
  });

  // ── /api/cases without session ─────────────────────────────────────────────

  describe("GET /api/cases (unauthenticated)", () => {
    it("returns 401 without a session cookie", async () => {
      const res = await app.inject({ method: "GET", url: "/api/cases" });
      expect(res.statusCode).toBe(401);
    });
  });

  // ── Role permissions ───────────────────────────────────────────────────────

  describe("Role permissions", () => {
    it("analyst role has can_resolve_conflicts = false", async () => {
      const analytEmail = `analyst-${randomUUID()}@test.com`;
      const passwordHash = await argon2.hash("analyst-test-pass-123", {
        type: argon2.argon2id, memoryCost: 4096, timeCost: 2, parallelism: 1,
      });
      createUser({
        id: randomUUID(),
        email: analytEmail,
        passwordHash,
        role: "analyst",
        atlasUserId: randomUUID(),
        encryptedApiKey: TEST_ENCRYPTED_KEY,
      });

      const loginRes = await app.inject({
        method: "POST",
        url: "/auth/login",
        payload: { email: analytEmail, password: "analyst-test-pass-123" },
      });
      expect(loginRes.statusCode).toBe(200);

      const body = JSON.parse(loginRes.body) as { permissions: Record<string, boolean> };
      expect(body.permissions["can_resolve_conflicts"]).toBe(false);
      expect(body.permissions["can_view_cases"]).toBe(true);
    });
  });

  // ── Health ─────────────────────────────────────────────────────────────────

  describe("GET /health", () => {
    it("returns 200", async () => {
      const res = await app.inject({ method: "GET", url: "/health" });
      expect(res.statusCode).toBe(200);
      expect(JSON.parse(res.body)).toMatchObject({ ok: true });
    });
  });
});
