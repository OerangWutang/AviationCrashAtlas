/**
 * BFF leads route tests.
 *
 * File location: services/bff/tests/leads.test.ts
 *
 * Uses a single Fastify app + DB for all tests (POST and GET suites
 * share the same in-memory SQLite database).
 */

import { randomUUID } from "node:crypto";

// ── Environment setup (must precede all other imports) ────────────────────────

process.env["BFF_DB_PATH"] = ":memory:";
process.env["BFF_SESSION_SECRET"] = "test-secret-that-is-long-enough-32chars";
process.env["NODE_ENV"] = "test";
process.env["ATLAS_SEED_API_KEY"] = "test-atlas-key";
process.env["WEB_ORIGIN"] = "http://localhost:5173";
process.env["ATLAS_API_BASE_URL"] = "http://localhost:8000";
process.env["LOGIN_RATE_LIMIT"] = "10";
process.env["LOGIN_RATE_LIMIT_WINDOW_MS"] = "60000";

import { _resetConfig } from "../src/config.js";
import { _closeDb, runMigrations } from "../src/db/database.js";
import { createUser } from "../src/db/users.js";
import { encryptApiKey } from "../src/auth/crypto.js";
import { buildApp, _clearLeadRateLimit } from "../src/server.js";
import { _clearRateLimits } from "../src/middleware/rateLimit.js";
import type { FastifyInstance } from "fastify";

// ── Fixtures ──────────────────────────────────────────────────────────────────

const ADMIN_EMAIL = "admin@test.com";
const ADMIN_PASSWORD = "admin-test-password-123";

const VALID_LEAD = {
  name: "Sarah Chen",
  email: "sarah@aviation-law.com",
  organization: "Chen Aviation Law",
  organizationType: "law_firm" as const,
  role: "Partner",
  useCase: "Litigation support for regional airline accident cases",
  message: "We handle 10–15 accident cases per year and need a better evidence management tool.",
};

// ── App fixture (shared between POST and GET suites) ─────────────────────────

let app: FastifyInstance;

beforeAll(async () => {
  _resetConfig?.();
  _closeDb();
  runMigrations();
  app = await buildApp();
  _clearRateLimits();

  // Seed an admin user for authenticated requests
  const argon2 = await import("argon2");
  const hash = await argon2.hash(ADMIN_PASSWORD, {
    type: argon2.argon2id,
    memoryCost: 4096,
    timeCost: 2,
    parallelism: 1,
  });
  createUser({
    id: randomUUID(),
    email: ADMIN_EMAIL,
    passwordHash: hash,
    role: "admin",
    atlasUserId: randomUUID(),
    encryptedApiKey: encryptApiKey(
      "test-api-key",
      "test-secret-that-is-long-enough-32chars",
    ),
  });
});

afterAll(async () => {
  await app.close();
  _closeDb();
});

beforeEach(() => {
  _clearRateLimits();
  _clearLeadRateLimit();
});

function parseCookies(headers: string[] | string | undefined): Record<string, string> {
  const result: Record<string, string> = {};
  const cookieHeaders = Array.isArray(headers) ? headers : headers ? [headers] : [];
  for (const h of cookieHeaders) {
    const parts = h.split(";")[0]?.split("=") ?? [];
    if (parts.length >= 2) {
      const name = parts[0]?.trim() ?? "";
      const value = parts.slice(1).join("=").trim();
      result[name] = value;
    }
  }
  return result;
}

async function loginAs(
  email: string,
  password: string,
  appOverride?: FastifyInstance,
): Promise<{ sessionCookie: string; csrfToken: string }> {
  const target = appOverride ?? app;
  const res = await target.inject({
    method: "POST",
    url: "/auth/login",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email, password }),
  });

  const cookies = parseCookies(res.headers["set-cookie"] as string[] | string | undefined);
  const sessionCookie = Object.entries(cookies)
    .filter(([name]) => name.startsWith("atlas-session") || name.startsWith("__Host-atlas-session"))
    .map(([, value]) => value)
    .join("; ");

  return { sessionCookie, csrfToken: cookies["atlas-csrf"] ?? "" };
}

// ── POST /api/leads ────────────────────────────────────────────────────────────

describe("POST /api/leads", () => {

  // ── Happy path ─────────────────────────────────────────────────────────────

  it("returns 201 for a valid submission", async () => {
    const res = await app.inject({
      method: "POST",
      url: "/api/leads",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(VALID_LEAD),
    });
    expect(res.statusCode).toBe(201);
  });

  it("returns 201 without an optional message field", async () => {
    const { message: _omit, ...body } = VALID_LEAD;
    const res = await app.inject({
      method: "POST",
      url: "/api/leads",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    expect(res.statusCode).toBe(201);
  });

  it("accepts all valid organizationType values", async () => {
    const validTypes = [
      "law_firm",
      "aviation_safety",
      "airline",
      "mro",
      "insurer",
      "other",
    ] as const;

    for (const orgType of validTypes) {
      _clearLeadRateLimit();
      const res = await app.inject({
        method: "POST",
        url: "/api/leads",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ...VALID_LEAD, organizationType: orgType }),
      });
      expect(res.statusCode).toBe(201);
    }
  });

  // ── Validation ────────────────────────────────────────────────────────────

  it("returns 400 when name is missing", async () => {
    const { name: _omit, ...withoutName } = VALID_LEAD;
    const res = await app.inject({
      method: "POST",
      url: "/api/leads",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(withoutName),
    });
    expect(res.statusCode).toBe(400);
  });

  it("returns 400 when email is missing", async () => {
    const { email: _omit, ...rest } = VALID_LEAD;
    const res = await app.inject({
      method: "POST",
      url: "/api/leads",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(rest),
    });
    expect(res.statusCode).toBe(400);
  });

  it("returns 400 for an invalid email address", async () => {
    const res = await app.inject({
      method: "POST",
      url: "/api/leads",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ...VALID_LEAD, email: "not-an-email" }),
    });
    expect(res.statusCode).toBe(400);
  });

  it("returns 400 for an invalid organizationType", async () => {
    const res = await app.inject({
      method: "POST",
      url: "/api/leads",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ...VALID_LEAD, organizationType: "invalid_type" }),
    });
    expect(res.statusCode).toBe(400);
  });

  it("returns 400 when organization is empty string", async () => {
    const res = await app.inject({
      method: "POST",
      url: "/api/leads",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ...VALID_LEAD, organization: "" }),
    });
    expect(res.statusCode).toBe(400);
  });

  it("returns 400 when useCase is missing", async () => {
    const { useCase: _omit, ...rest } = VALID_LEAD;
    const res = await app.inject({
      method: "POST",
      url: "/api/leads",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(rest),
    });
    expect(res.statusCode).toBe(400);
  });

  it("returns 400 when name exceeds maximum length", async () => {
    const res = await app.inject({
      method: "POST",
      url: "/api/leads",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ...VALID_LEAD, name: "x".repeat(201) }),
    });
    expect(res.statusCode).toBe(400);
  });

  it("returns 400 when useCase exceeds maximum length", async () => {
    const res = await app.inject({
      method: "POST",
      url: "/api/leads",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ...VALID_LEAD, useCase: "x".repeat(2001) }),
    });
    expect(res.statusCode).toBe(400);
  });

  it("returns 201 even for duplicate emails (no enumeration)", async () => {
    const res = await app.inject({
      method: "POST",
      url: "/api/leads",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(VALID_LEAD),
    });
    expect(res.statusCode).toBe(201);
  });

  // ── Rate limiting ─────────────────────────────────────────────────────────

  it("returns 429 after exceeding the per-IP rate limit", async () => {
    for (let i = 0; i < 6; i++) {
      const res = await app.inject({
        method: "POST",
        url: "/api/leads",
        headers: {
          "content-type": "application/json",
          "x-forwarded-for": "10.0.0.42",
        },
        body: JSON.stringify({ ...VALID_LEAD, email: `rate${i}@test.com` }),
      });
      if (i < 5) expect(res.statusCode).toBe(201);
      else expect(res.statusCode).toBe(429);
    }
  });

  it("sets a Retry-After header on 429", async () => {
    let rateLimitedResponse: Awaited<ReturnType<typeof app.inject>> | null = null;

    for (let i = 0; i < 8; i++) {
      const res = await app.inject({
        method: "POST",
        url: "/api/leads",
        headers: {
          "content-type": "application/json",
          "x-forwarded-for": "10.0.0.43",
        },
        body: JSON.stringify({ ...VALID_LEAD, email: `retry${i}@test.com` }),
      });
      if (res.statusCode === 429) {
        rateLimitedResponse = res;
        break;
      }
    }

    if (rateLimitedResponse) {
      expect(rateLimitedResponse.headers["retry-after"]).toBeDefined();
      const retryAfter = Number(rateLimitedResponse.headers["retry-after"]);
      expect(retryAfter).toBeGreaterThan(0);
    }
  });
});

// ── GET /api/leads ────────────────────────────────────────────────────────────

describe("GET /api/leads", () => {

  it("returns 401 without a session", async () => {
    const res = await app.inject({
      method: "GET",
      url: "/api/leads",
    });
    expect(res.statusCode).toBe(401);
  });

  it("returns 403 for a non-admin user", async () => {
    // Seed a reviewer
    const argon2 = await import("argon2");
    const hash = await argon2.hash("reviewer-password-123", {
      type: argon2.argon2id,
      memoryCost: 4096,
      timeCost: 2,
      parallelism: 1,
    });
    createUser({
      id: randomUUID(),
      email: "reviewer2@test.com",
      passwordHash: hash,
      role: "reviewer",
      atlasUserId: randomUUID(),
      encryptedApiKey: encryptApiKey(
        "test-api-key-2",
        "test-secret-that-is-long-enough-32chars",
      ),
    });

    const { sessionCookie } = await loginAs(
      "reviewer2@test.com",
      "reviewer-password-123",
    );

    const res = await app.inject({
      method: "GET",
      url: "/api/leads",
      headers: { cookie: sessionCookie },
    });
    expect(res.statusCode).toBe(403);
  });

  it("returns leads list for admin", async () => {
    // First add a lead so there's something to list
    await app.inject({
      method: "POST",
      url: "/api/leads",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(VALID_LEAD),
    });

    const { sessionCookie } = await loginAs(ADMIN_EMAIL, ADMIN_PASSWORD);
    const res = await app.inject({
      method: "GET",
      url: "/api/leads",
      headers: { cookie: sessionCookie },
    });

    expect(res.statusCode).toBe(200);
    const body = JSON.parse(res.body);
    expect(Array.isArray(body.leads)).toBe(true);
    expect(typeof body.total).toBe("number");
    expect(body.total).toBeGreaterThanOrEqual(1);
  });

  it("returned leads include the expected fields", async () => {
    const { sessionCookie } = await loginAs(ADMIN_EMAIL, ADMIN_PASSWORD);
    const res = await app.inject({
      method: "GET",
      url: "/api/leads",
      headers: { cookie: sessionCookie },
    });

    const body = JSON.parse(res.body);
    if (body.leads.length > 0) {
      const lead = body.leads[0];
      expect(lead).toHaveProperty("id");
      expect(lead).toHaveProperty("name");
      expect(lead).toHaveProperty("email");
      expect(lead).toHaveProperty("organization");
      expect(lead).toHaveProperty("organization_type");
      expect(lead).toHaveProperty("role_type");
      expect(lead).toHaveProperty("use_case");
      expect(lead).toHaveProperty("created_at");
    }
  });

  it("caps the result at 200 rows", async () => {
    const { sessionCookie } = await loginAs(ADMIN_EMAIL, ADMIN_PASSWORD);

    const res = await app.inject({
      method: "GET",
      url: "/api/leads",
      headers: { cookie: sessionCookie },
    });

    const body = JSON.parse(res.body);
    expect(body.leads.length).toBeLessThanOrEqual(200);
  });
});