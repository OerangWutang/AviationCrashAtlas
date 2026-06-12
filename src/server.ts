/**
 * BFF server bootstrap.
 *
 * Fastify app with:
 *   - Cookie plugin (HttpOnly session + CSRF cookies)
 *   - CORS locked to WEB_ORIGIN with credentials: true
 *   - CSRF enforcement on all mutating requests
 *   - /auth/* routes (login, me, logout)
 *   - /api/cases/* proxy routes
 *   - /health endpoint
 *
 * The browser SPA points VITE_API_BASE_URL at this server.
 * FastAPI is never contacted directly from the browser.
 */

import path from "node:path";
import { fileURLToPath } from "node:url";
import Fastify from "fastify";
import cookie from "@fastify/cookie";
import cors from "@fastify/cors";
import multipart from "@fastify/multipart";
import fastifyStatic from "@fastify/static";
import { getConfig } from "./config.js";
import { runMigrations } from "./db/database.js";
import { registerAuthRoutes } from "./auth/routes.js";
import { registerCasesRoutes } from "./cases/routes.js";
import { sweepExpiredSessions } from "./db/users.js";
import { sweepExpiredEntries, sweepLoginEntries } from "./middleware/rateLimit.js";
import { requireSession, checkCsrf } from "./auth/routes.js";
import { decryptApiKey, encryptApiKey } from "./auth/crypto.js";
import { camelizeKeys } from "./atlasClient.js";
import { createLead, listLeads, countLeads, VALID_ORG_TYPES } from "./db/leads.js";
import { createUser, findUserByEmail } from "./db/users.js";
import { checkRateLimit } from "./middleware/rateLimit.js";
import { initRedisRateLimiter, shutdownRedisRateLimiter, checkRateLimitRedis } from "./middleware/rateLimitRedis.js";
import { randomUUID } from "node:crypto";
import { fetch as undiciFetch, FormData as UndiciFormData } from "undici";
import argon2 from "argon2";

// Optional PG imports — only loaded when BFF_DATABASE_URL is set
let pgPool: any = null;
let pgRepos: typeof import("./db/repos-pg.js") | null = null;
let dbSetup: typeof import("./db/database-pg.js") | null = null;
let _dbUrl = "";
import type { FastifyRequest, FastifyReply } from "fastify";


export async function buildApp() {
  const config = getConfig();

  const app = Fastify({
    logger: {
      level: config.isProduction ? "info" : "debug",
      // Never log request bodies — they may contain passwords
      serializers: {
        req(req) {
          return { method: req.method, url: req.url, remoteAddress: req.socket?.remoteAddress };
        },
      },
    },
    trustProxy: true, // Required for correct req.ip behind Caddy/nginx
  });

  // ── Redis init ──────────────────────────────────────────────────────────────
  // Redis-backed rate limiting transparently falls back to in-process if
  // REDIS_URL is not configured or the connection fails.
  const redisActive = await initRedisRateLimiter(config.redisUrl ?? undefined);
  if (config.redisUrl && redisActive) {
    app.log.info("Redis-backed rate limiter active");
  } else if (config.redisUrl && !redisActive) {
    app.log.warn("Redis configured but connection failed — falling back to in-process rate limiter");
  }

  // ── Plugins ────────────────────────────────────────────────────────────────

  await app.register(cookie);

  await app.register(cors, {
    origin: config.webOrigin,
    credentials: true,
    methods: ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allowedHeaders: ["Content-Type", "X-CSRF-Token"],
    exposedHeaders: ["Retry-After"],
  });

  await app.register(multipart, {
    limits: { fileSize: 50 * 1024 * 1024 }, // 50 MB
  });

  // ── Health ─────────────────────────────────────────────────────────────────

  app.get("/health", async () => ({ ok: true, service: "atlas-bff" }));

  // ── Document upload (multipart proxy) ──────────────────────────────────────
  // Multipart requires special handling — can't use atlasRequest for this.

  app.post(
    "/api/documents",
    async (req: FastifyRequest, reply: FastifyReply) => {
      const user = await requireSession(req, reply);
      if (!user) return;
      if (!checkCsrf(req, reply)) return;
      const ip = req.ip;
      let rl;
      if (config.redisUrl) {
        rl = await checkRateLimitRedis(ip, 10, 60000);
      } else {
        rl = checkRateLimit(ip, 10, 60000);
      }
      if (!rl.allowed) {
        return reply.status(429).header("Retry-After", String(Math.ceil(rl.retryAfterMs / 1000))).send({ error: "Too many requests" });
      }

      try {
        const data = await req.file();
        if (!data) {
          return reply.status(400).send({ error: "No file uploaded" });
        }

        const fileBuffer = await data.toBuffer();
        const formData = new UndiciFormData();
        formData.append(
          "file",
          new File([fileBuffer], data.filename ?? "document.pdf", {
            type: data.mimetype,
          }),
          data.filename ?? "document.pdf",
        );

        // Forward query params (event_id, source_name)
        const url = new URL(req.url, "http://localhost");
        const qs = url.searchParams.toString();
        const upstream = `${config.atlasApiBaseUrl}/api/v1/documents${qs ? `?${qs}` : ""}`;

        const apiKey = decryptApiKey(user.encryptedApiKey, config.sessionSecret);
        const response = await undiciFetch(upstream, {
          method: "POST",
          headers: { "X-API-Key": apiKey },
          body: formData,
        });

        const camelized = camelizeKeys(await response.json());
        return reply.status(response.status).send(camelized);
      } catch (err) {
        app.log.error({ err }, "Document upload failed");
        return reply.status(502).send({ error: "Upload failed" });
      }
    },
  );

  // ── Marketing site (static files) ─────────────────────────────────────────

  const __dirname = path.dirname(fileURLToPath(import.meta.url));
  const marketingDir = path.resolve(__dirname, "../../../apps/marketing");
  await app.register(fastifyStatic, {
    root: marketingDir,
    prefix: "/",
    wildcard: false,
  });

  // ── Leads routes ───────────────────────────────────────────────────────────

  app.post("/api/leads", async (req: FastifyRequest, reply: FastifyReply) => {
    // Rate limit: 5 per 10 min per IP
    const ip = req.ip;
    let rl;
    if (config.redisUrl) {
      rl = await checkRateLimitRedis(ip, 5, 600000);
    } else {
      rl = checkRateLimit(ip, 5, 600000);
    }
    if (!rl.allowed) {
      return reply.status(429).header("Retry-After", String(Math.ceil(rl.retryAfterMs / 1000))).send({ error: "Rate limit exceeded" });
    }

    const body = req.body as Record<string, unknown>;
    const { name, email, organization, roleType, organizationType, useCase, message } = body ?? {};

    // Validation
    const errors: string[] = [];
    if (!name || typeof name !== "string" || name.trim().length === 0) errors.push("name is required");
    if (typeof name === "string" && name.length > 200) errors.push("name exceeds 200 characters");
    if (!email || typeof email !== "string") errors.push("email is required");
    if (typeof email === "string" && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) errors.push("invalid email");
    if (!organization || typeof organization !== "string" || organization.trim().length === 0) errors.push("organization is required");
    if (typeof roleType !== "undefined" && (typeof roleType !== "string" || roleType.length > 200)) errors.push("roleType must be a string of at most 200 characters");
    if (!useCase || typeof useCase !== "string" || useCase.trim().length === 0) errors.push("useCase is required");
    if (typeof useCase === "string" && useCase.length > 2000) errors.push("useCase exceeds 2000 characters");
    if (!VALID_ORG_TYPES.has(organizationType as string)) errors.push("invalid organizationType");

    if (errors.length > 0) {
      return reply.status(400).send({ ok: false, errors });
    }

    try {
      await createLead({
        name: String(name).trim(),
        email: String(email).trim(),
        organization: String(organization).trim(),
        roleType: String(roleType ?? "").trim(),
        organizationType: String(organizationType),
        useCase: String(useCase).trim(),
        message: message ? String(message).trim() : undefined,
      });
    } catch {
      // Always return 201 to prevent enumeration
    }

    return reply.status(201).send({ ok: true });
  });

  app.get("/api/leads", async (req: FastifyRequest, reply: FastifyReply) => {
    const user = await requireSession(req, reply);
    if (!user) return;
    if (user.role !== "admin") {
      return reply.status(403).send({ error: "Admin only" });
    }
    const [leads, total] = await Promise.all([listLeads(200), countLeads()]);
    return reply.send({
      leads: leads.map((l) => ({
        id: l.id,
        name: l.name,
        email: l.email,
        organization: l.organization,
        organization_type: l.organizationType,
        role_type: l.roleType,
        use_case: l.useCase,
        message: l.message,
        created_at: l.createdAt,
      })),
      total,
    });
  });

  // ── Routes ─────────────────────────────────────────────────────────────────

  await registerAuthRoutes(app);
  await registerCasesRoutes(app);

  return app;
}

export async function startServer(): Promise<void> {
  const config = getConfig();

  // Use PostgreSQL when BFF_DATABASE_URL is set, otherwise SQLite
  _dbUrl = process.env["BFF_DATABASE_URL"] ?? "";
  if (_dbUrl) {
    dbSetup = await import("./db/database-pg.js");
    pgPool = await dbSetup.createPgPool(_dbUrl);
    pgRepos = await import("./db/repos-pg.js");
    console.log(`[bff] Connected to PostgreSQL at ${_dbUrl.replace(/\/\/.*@/, "//***@")}`);
  } else {
    // Run SQLite migrations
    runMigrations();
  }

  // Auto-seed default users so the BFF is usable immediately after restart
  await seedDefaultUsers();

  const app = await buildApp();

  // Periodic housekeeping: clear expired sessions + rate-limit entries
  setInterval(
    () => {
      try {
        if (_dbUrl && pgRepos) {
          pgRepos.sweepExpiredSessionsPg().catch((err: Error) => console.warn("Failed to sweep PG sessions:", err));
        } else {
          sweepExpiredSessions();
        }
      } catch (err) {
        app.log.warn({ err }, "Failed to sweep expired sessions");
      }
      sweepExpiredEntries(60000);
      sweepLoginEntries(config.loginRateLimitWindowMs);
    },
    10 * 60 * 1000, // every 10 minutes
  );

  try {
    await app.listen({ port: config.port, host: config.host });
    app.log.info(`Atlas BFF listening on ${config.host}:${config.port}`);
    app.log.info(`Proxying to Atlas API at ${config.atlasApiBaseUrl}`);
    app.log.info(`Accepting requests from ${config.webOrigin}`);
  } catch (err) {
    app.log.error(err);
    process.exit(1);
  }
}

// ── Auto-seed default users ──────────────────────────────────────────────────

async function seedDefaultUsers(): Promise<void> {
  const seedPassword = process.env["BFF_SEED_PASSWORD"];
  if (!seedPassword) {
    console.warn("Skipping auto-seed: BFF_SEED_PASSWORD not set");
    return;
  }

  const defaultUsers = [
    {
      email: "reviewer@atlas.local",
      password: seedPassword,
      role: "reviewer" as const,
      atlasUserId: "07b2bb7a-6c11-4271-b1e2-f9a1c097c21c",
      atlasApiKey: process.env["ATLAS_RAW_API_KEY"] || process.env["ATLAS_SEED_API_KEY"] || "",
    },
    {
      email: "admin@atlas.local",
      password: seedPassword,
      role: "admin" as const,
      atlasUserId: "3e566c69-b8bb-444d-b90d-6edee25be589",
      atlasApiKey: process.env["ATLAS_RAW_API_KEY"] || process.env["ATLAS_SEED_API_KEY"] || "",
    },
  ];

  for (const u of defaultUsers) {
    const existing = _dbUrl
      ? await pgRepos!.findUserByEmailPg(u.email)
      : findUserByEmail(u.email);
    if (existing) continue;

    if (!u.atlasApiKey) {
      console.warn(`Skipping seed for ${u.email}: ATLAS_RAW_API_KEY not set`);
      continue;
    }

    const passwordHash = await argon2.hash(u.password, {
      type: argon2.argon2id,
      memoryCost: 65536,
      timeCost: 3,
      parallelism: 4,
    });

    const encryptedApiKey = encryptApiKey(
      u.atlasApiKey,
      getConfig().sessionSecret,
    );

    if (_dbUrl) {
      await pgRepos!.createUserPg({
        id: randomUUID(),
        email: u.email,
        passwordHash,
        role: u.role,
        atlasUserId: u.atlasUserId,
        encryptedApiKey,
      });
    } else {
      createUser({
        id: randomUUID(),
        email: u.email,
        passwordHash,
        role: u.role,
        atlasUserId: u.atlasUserId,
        encryptedApiKey,
      });
    }

    console.log(`Auto-seeded user: ${u.email} (${u.role})`);
  }
}

// ── Entrypoint ─────────────────────────────────────────────────────────────

const isMain =
  process.argv[1] !== undefined &&
  new URL(import.meta.url).pathname === new URL(process.argv[1], import.meta.url).pathname;

if (isMain) {
  void startServer();
}
