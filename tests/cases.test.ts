import { randomUUID } from "node:crypto";
import { unlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import argon2 from "argon2";
import { jest } from "@jest/globals";
import type { FastifyInstance } from "fastify";

const dbPath = path.join(tmpdir(), `atlas-bff-cases-${process.pid}.db`);
process.env["BFF_DB_PATH"] = dbPath;
process.env["BFF_SESSION_SECRET"] = "test-secret-that-is-long-enough-32chars";
process.env["NODE_ENV"] = "test";
process.env["ATLAS_SEED_API_KEY"] = "test-atlas-key";
process.env["WEB_ORIGIN"] = "http://localhost:5173";
process.env["ATLAS_API_BASE_URL"] = "http://localhost:8000";
process.env["LOGIN_RATE_LIMIT"] = "5";
process.env["LOGIN_RATE_LIMIT_WINDOW_MS"] = "60000";

const fetchMock = jest.fn<(...args: unknown[]) => Promise<unknown>>();

await jest.unstable_mockModule("undici", async () => {
  const actual = await jest.requireActual<typeof import("undici")>("undici");
  return {
    ...actual,
    fetch: fetchMock,
  };
});

const { _resetConfig } = await import("../src/config.js");
const { _closeDb, runMigrations } = await import("../src/db/database.js");
const { createUser } = await import("../src/db/users.js");
const { encryptApiKey } = await import("../src/auth/crypto.js");
const { buildApp } = await import("../src/server.js");
const { _clearRateLimits } = await import("../src/middleware/rateLimit.js");
const { _clearSlugCache } = await import("../src/cases/routes.js");

const TEST_EMAIL = "case-reviewer@test.com";
const TEST_PASSWORD = "test-password-secure-123";
const TEST_ATLAS_USER_ID = randomUUID();

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

async function seedTestUser(): Promise<void> {
  const passwordHash = await argon2.hash(TEST_PASSWORD, {
    type: argon2.argon2id,
    memoryCost: 4096,
    timeCost: 2,
    parallelism: 1,
  });

  createUser({
    id: randomUUID(),
    email: TEST_EMAIL,
    passwordHash,
    role: "reviewer",
    atlasUserId: TEST_ATLAS_USER_ID,
    encryptedApiKey: encryptApiKey(
      "test-raw-api-key",
      "test-secret-that-is-long-enough-32chars",
    ),
  });
}

function parseCookies(headers: string[] | string | undefined): Record<string, string> {
  const result: Record<string, string> = {};
  const cookieHeaders = Array.isArray(headers) ? headers : headers ? [headers] : [];
  for (const header of cookieHeaders) {
    const [name, ...valueParts] = header.split(";")[0]?.split("=") ?? [];
    if (name && valueParts.length > 0) {
      result[name.trim()] = valueParts.join("=").trim();
    }
  }
  return result;
}

describe("BFF case routes", () => {
  let app: FastifyInstance | null = null;
  let sessionCookies: Record<string, string>;

  beforeAll(async () => {
    _resetConfig();
    runMigrations();
    await seedTestUser();
    app = await buildApp();

    const loginRes = await app.inject({
      method: "POST",
      url: "/auth/login",
      payload: { email: TEST_EMAIL, password: TEST_PASSWORD },
    });

    sessionCookies = parseCookies(loginRes.headers["set-cookie"]);
  });

  afterAll(async () => {
    await app?.close();
    _closeDb();
    for (const suffix of ["", "-shm", "-wal"]) {
      try {
        unlinkSync(`${dbPath}${suffix}`);
      } catch {
        // Test cleanup is best-effort.
      }
    }
  });

  beforeEach(() => {
    fetchMock.mockReset();
    _clearRateLimits();
    _clearSlugCache();
  });

  it("resolves a case slug before proxying field explanations by event id", async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({
          canonical_event_id: "event-1",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          event_id: "event-1",
          field_name: "operator",
          has_winner: true,
          winner: {
            field_name: "operator",
            current_value: "Colgan Air",
            plain_english: "NTSB final report supports this operator.",
            source_name: "NTSB eADMS",
            source_kind: "GOVERNMENT",
          },
          losers: [],
          losers_truncated: false,
          conflict: null,
        }),
      );

    const res = await app!.inject({
      method: "GET",
      url: "/api/cases/colgan-air-3407/audit/fields/operator/explanation",
      cookies: sessionCookies,
    });

    expect(res.statusCode).toBe(200);
    expect(JSON.parse(res.body)).toMatchObject({
      eventId: "event-1",
      fieldName: "operator",
      winner: {
        sourceName: "NTSB eADMS",
      },
    });
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "http://localhost:8000/api/v1/public/events/colgan-air-3407",
      expect.objectContaining({
        headers: expect.objectContaining({ "X-API-Key": "test-raw-api-key" }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://localhost:8000/api/v1/audit/events/event-1/fields/operator/explanation",
      expect.objectContaining({
        headers: expect.objectContaining({ "X-API-Key": "test-raw-api-key" }),
      }),
    );
  });

  it("returns 404 when slug resolution fails", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "Not found" }, 404));

    const res = await app!.inject({
      method: "GET",
      url: "/api/cases/missing/audit/fields/operator/explanation",
      cookies: sessionCookies,
    });

    expect(res.statusCode).toBe(404);
    expect(JSON.parse(res.body)).toEqual({ error: "Case not found" });
  });
});
