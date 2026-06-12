/**
 * Cases proxy routes.
 *
 * Proxies authenticated requests to Atlas /api/v1/public/events.
 * The browser never sees the X-API-Key — it's injected server-side by atlasClient.
 *
 * Route surface:
 *   GET /api/cases                 → GET /api/v1/public/events
 *   GET /api/cases/:slug           → GET /api/v1/public/events/:slug
 *   GET /api/cases/:slug/evidence  → GET /api/v1/public/events/:slug/evidence
 *   GET /api/cases/:slug/timeline  → GET /api/v1/public/events/:slug/timeline
 *   GET /api/cases/:slug/provenance → GET /api/v1/accidents/:event_id/provenance
 *   GET /api/cases/:slug/conflicts → GET /api/v1/conflicts?event_id=
 *   GET /api/cases/:slug/audit     → GET /api/v1/public/events/:slug/audit
 *
 * The BFF owns slug↔event_id resolution by caching the /public/events list.
 * This means the SPA uses a single consistent identifier (slug) and never
 * needs to know whether a downstream call needs a slug or a UUID.
 */

import type { FastifyInstance, FastifyRequest, FastifyReply } from "fastify";
import { fetch as undiciFetch } from "undici";
import { atlasRequest, AtlasUpstreamError } from "../atlasClient.js";
import { requireSession, checkCsrf } from "../auth/routes.js";
import type { SessionUser } from "../auth/sessions.js";
import { checkRateLimit } from "../middleware/rateLimit.js";
import { decryptApiKey } from "../auth/crypto.js";
import { getConfig } from "../config.js";

// ── Slug → event_id cache (in-process, sufficient for MVP) ──────────────────

const SLUG_CACHE_TTL_MS = 5 * 60 * 1000; // re-fetch after 5 minutes so stale IDs don't persist
const SLUG_CACHE_MAX = 2000;              // cap memory footprint

const _slugCache = new Map<string, { eventId: string; cachedAt: number }>();

async function resolveEventId(slug: string, user: SessionUser): Promise<string | null> {
  const cached = _slugCache.get(slug);
  if (cached && Date.now() - cached.cachedAt < SLUG_CACHE_TTL_MS) {
    return cached.eventId;
  }

  // Fetch the specific case detail to resolve slug → event_id
  try {
    const detail = await atlasRequest<{ canonicalEventId?: string }>(
      `/api/v1/public/events/${slug}`,
      user,
    );
    if (detail.canonicalEventId) {
      // Evict the oldest entry if the cache is at capacity
      if (_slugCache.size >= SLUG_CACHE_MAX) {
        const firstKey = _slugCache.keys().next().value;
        if (firstKey !== undefined) _slugCache.delete(firstKey);
      }
      _slugCache.set(slug, { eventId: detail.canonicalEventId, cachedAt: Date.now() });
      return detail.canonicalEventId;
    }
  } catch {
    return null;
  }

  return null;
}

/** Clear cache (tests and admin operations). */
export function _clearSlugCache(): void {
  _slugCache.clear();
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function upstreamUrl(base: string, req: FastifyRequest): string {
  const qs = new URL(req.url, "http://localhost").searchParams.toString();
  return qs ? `${base}?${qs}` : base;
}

// ── Error normalizer ─────────────────────────────────────────────────────────

function handleUpstreamError(err: unknown, reply: FastifyReply): FastifyReply {
  if (err instanceof AtlasUpstreamError) {
    return reply.status(err.status).send(err.body);
  }
  return reply.status(502).send({ error: "Upstream service unavailable" });
}

// ── Route registration ───────────────────────────────────────────────────────

export async function registerCasesRoutes(app: FastifyInstance): Promise<void> {

  // GET /api/cases — list all public events
  app.get("/api/cases", async (req: FastifyRequest, reply: FastifyReply) => {
    const user = await requireSession(req, reply);
    if (!user) return;

    try {
      const qs = new URL(req.url, "http://localhost").searchParams.toString();
      const path = qs ? `/api/v1/public/events?${qs}` : "/api/v1/public/events?limit=100";
      const data = await atlasRequest(path, user);
      return reply.send(data);
    } catch (err) {
      return handleUpstreamError(err, reply);
    }
  });

  // GET /api/cases/:slug — case detail
  app.get(
    "/api/cases/:slug",
    async (req: FastifyRequest<{ Params: { slug: string } }>, reply: FastifyReply) => {
      const user = await requireSession(req, reply);
      if (!user) return;

      try {
        const data = await atlasRequest(`/api/v1/public/events/${req.params.slug}`, user);
        return reply.send(data);
      } catch (err) {
        return handleUpstreamError(err, reply);
      }
    },
  );

  // GET /api/cases/:slug/evidence
  app.get(
    "/api/cases/:slug/evidence",
    async (req: FastifyRequest<{ Params: { slug: string } }>, reply: FastifyReply) => {
      const user = await requireSession(req, reply);
      if (!user) return;

      try {
        const data = await atlasRequest(
          `/api/v1/public/events/${req.params.slug}/evidence`,
          user,
        );
        return reply.send(data);
      } catch (err) {
        return handleUpstreamError(err, reply);
      }
    },
  );

  // GET /api/cases/:slug/timeline
  app.get(
    "/api/cases/:slug/timeline",
    async (req: FastifyRequest<{ Params: { slug: string } }>, reply: FastifyReply) => {
      const user = await requireSession(req, reply);
      if (!user) return;

      try {
        const data = await atlasRequest(
          `/api/v1/public/events/${req.params.slug}/timeline`,
          user,
        );
        return reply.send(data);
      } catch (err) {
        return handleUpstreamError(err, reply);
      }
    },
  );

  // GET /api/cases/:slug/audit
  app.get(
    "/api/cases/:slug/audit",
    async (req: FastifyRequest<{ Params: { slug: string } }>, reply: FastifyReply) => {
      const user = await requireSession(req, reply);
      if (!user) return;

      try {
        const data = await atlasRequest(
          `/api/v1/public/events/${req.params.slug}/audit`,
          user,
        );
        return reply.send(data);
      } catch (err) {
        return handleUpstreamError(err, reply);
      }
    },
  );

  // GET /api/cases/:slug/audit/fields/:fieldName/explanation
  app.get(
    "/api/cases/:slug/audit/fields/:fieldName/explanation",
    async (
      req: FastifyRequest<{
        Params: { slug: string; fieldName: string };
        Querystring: Record<string, string>;
      }>,
      reply: FastifyReply,
    ) => {
      const user = await requireSession(req, reply);
      if (!user) return;

      const eventId = await resolveEventId(req.params.slug, user);
      if (!eventId) {
        return reply.status(404).send({ error: "Case not found" });
      }

      try {
        const fieldName = encodeURIComponent(req.params.fieldName);
        const data = await atlasRequest(
          upstreamUrl(`/api/v1/audit/events/${eventId}/fields/${fieldName}/explanation`, req),
          user,
        );
        return reply.send(data);
      } catch (err) {
        return handleUpstreamError(err, reply);
      }
    },
  );

  // GET /api/cases/:slug/provenance — needs slug→event_id resolution
  app.get(
    "/api/cases/:slug/provenance",
    async (req: FastifyRequest<{ Params: { slug: string }; Querystring: Record<string, string> }>, reply: FastifyReply) => {
      const user = await requireSession(req, reply);
      if (!user) return;

      const eventId = await resolveEventId(req.params.slug, user);
      if (!eventId) {
        return reply.status(404).send({ error: "Case not found" });
      }

      try {
        const data = await atlasRequest(upstreamUrl(`/api/v1/accidents/${eventId}/provenance`, req), user);
        return reply.send(data);
      } catch (err) {
        return handleUpstreamError(err, reply);
      }
    },
  );

  // GET /api/cases/:slug/conflicts — needs slug→event_id resolution
  app.get(
    "/api/cases/:slug/conflicts",
    async (req: FastifyRequest<{ Params: { slug: string }; Querystring: Record<string, string> }>, reply: FastifyReply) => {
      const user = await requireSession(req, reply);
      if (!user) return;

      const eventId = await resolveEventId(req.params.slug, user);
      if (!eventId) {
        return reply.status(404).send({ error: "Case not found" });
      }

      try {
        const url = new URL(req.url, "http://localhost");
        url.searchParams.set("event_id", eventId);
        const data = await atlasRequest(
          `/api/v1/conflicts?${url.searchParams.toString()}`,
          user,
        );
        return reply.send(data);
      } catch (err) {
        return handleUpstreamError(err, reply);
      }
    },
  );

  // ── Conflict operations (no slug needed — conflict_id is stable) ──────────

  // GET /api/conflicts/:id
  app.get(
    "/api/conflicts/:id",
    async (req: FastifyRequest<{ Params: { id: string } }>, reply: FastifyReply) => {
      const user = await requireSession(req, reply);
      if (!user) return;
      try {
        return reply.send(await atlasRequest(`/api/v1/conflicts/${req.params.id}`, user));
      } catch (err) { return handleUpstreamError(err, reply); }
    },
  );

  // GET /api/conflicts/:id/candidates
  app.get(
    "/api/conflicts/:id/candidates",
    async (req: FastifyRequest<{ Params: { id: string } }>, reply: FastifyReply) => {
      const user = await requireSession(req, reply);
      if (!user) return;
      try {
        return reply.send(await atlasRequest(`/api/v1/conflicts/${req.params.id}/candidates`, user));
      } catch (err) { return handleUpstreamError(err, reply); }
    },
  );

  // GET /api/conflicts/:id/history
  app.get(
    "/api/conflicts/:id/history",
    async (req: FastifyRequest<{ Params: { id: string }; Querystring: Record<string, string> }>, reply: FastifyReply) => {
      const user = await requireSession(req, reply);
      if (!user) return;
      try {
        return reply.send(await atlasRequest(upstreamUrl(`/api/v1/conflicts/${req.params.id}/history`, req), user));
      } catch (err) { return handleUpstreamError(err, reply); }
    },
  );

  // POST /api/conflicts/:id/resolve
  app.post(
    "/api/conflicts/:id/resolve",
    async (req: FastifyRequest<{ Params: { id: string }; Body: unknown }>, reply: FastifyReply) => {
      const user = await requireSession(req, reply);
      if (!user) return;
      if (!checkCsrf(req, reply)) return;
      const ip = req.ip;
      const rl = checkRateLimit(ip, 30, 60000);
      if (!rl.allowed) {
        return reply.status(429).header("Retry-After", String(Math.ceil(rl.retryAfterMs / 1000))).send({ error: "Too many requests" });
      }
      try {
        const data = await atlasRequest(
          `/api/v1/conflicts/${req.params.id}/resolve`,
          user,
          { method: "POST", body: JSON.stringify(req.body) },
        );
        return reply.send(data);
      } catch (err) { return handleUpstreamError(err, reply); }
    },
  );

  // POST /api/conflicts/:id/reopen
  app.post(
    "/api/conflicts/:id/reopen",
    async (req: FastifyRequest<{ Params: { id: string }; Body: unknown }>, reply: FastifyReply) => {
      const user = await requireSession(req, reply);
      if (!user) return;
      if (!checkCsrf(req, reply)) return;
      const ip = req.ip;
      const rl = checkRateLimit(ip, 30, 60000);
      if (!rl.allowed) {
        return reply.status(429).header("Retry-After", String(Math.ceil(rl.retryAfterMs / 1000))).send({ error: "Too many requests" });
      }
      try {
        const data = await atlasRequest(
          `/api/v1/conflicts/${req.params.id}/reopen`,
          user,
          { method: "POST", body: JSON.stringify(req.body) },
        );
        return reply.send(data);
      } catch (err) { return handleUpstreamError(err, reply); }
    },
  );

  // ── Report routes (no slug needed — event_id in path) ────────────────────

  // GET /api/reports/:eventId/preview
  app.get(
    "/api/reports/:eventId/preview",
    async (req: FastifyRequest<{ Params: { eventId: string } }>, reply: FastifyReply) => {
      const user = await requireSession(req, reply);
      if (!user) return;
      try {
        return reply.send(await atlasRequest(`/api/v1/reports/${req.params.eventId}/preview`, user));
      } catch (err) { return handleUpstreamError(err, reply); }
    },
  );

  // POST /api/reports/:eventId/generate
  app.post(
    "/api/reports/:eventId/generate",
    async (req: FastifyRequest<{ Params: { eventId: string }; Querystring: Record<string, string> }>, reply: FastifyReply) => {
      const user = await requireSession(req, reply);
      if (!user) return;
      if (!checkCsrf(req, reply)) return;
      const ip = req.ip;
      const rl = checkRateLimit(ip, 20, 60000);
      if (!rl.allowed) {
        return reply.status(429).header("Retry-After", String(Math.ceil(rl.retryAfterMs / 1000))).send({ error: "Too many requests" });
      }
      try {
        const data = await atlasRequest(upstreamUrl(`/api/v1/reports/${req.params.eventId}/generate`, req), user, { method: "POST" });
        return reply.send(data);
      } catch (err) { return handleUpstreamError(err, reply); }
    },
  );

  // GET /api/reports (list, with ?event_id=)
  app.get(
    "/api/reports",
    async (req: FastifyRequest<{ Querystring: Record<string, string> }>, reply: FastifyReply) => {
      const user = await requireSession(req, reply);
      if (!user) return;
      try {
        return reply.send(await atlasRequest(upstreamUrl("/api/v1/reports", req), user));
      } catch (err) { return handleUpstreamError(err, reply); }
    },
  );

  // GET /api/reports/:reportId/download?format=html|pdf
  app.get(
    "/api/reports/:reportId/download",
    async (req: FastifyRequest<{ Params: { reportId: string }; Querystring: Record<string, string> }>, reply: FastifyReply) => {
      const user = await requireSession(req, reply);
      if (!user) return;
      const format = (req.query as Record<string, string>).format ?? "html";
      if (format !== "html" && format !== "pdf") {
        return reply.status(400).send({ error: "format must be 'html' or 'pdf'" });
      }
      try {
        // Bypass atlasRequest — download returns HTML or PDF bytes, not JSON
        const config = getConfig();
        const apiKey = decryptApiKey(user.encryptedApiKey, config.sessionSecret);
        const upstream = await undiciFetch(
          `${config.atlasApiBaseUrl}/api/v1/reports/${req.params.reportId}/download?format=${format}`,
          { headers: { "X-API-Key": apiKey } },
        );
        if (!upstream.ok) {
          return reply.status(upstream.status).send({ error: "Report not found" });
        }
        const contentType = upstream.headers.get("content-type") ??
          (format === "pdf" ? "application/pdf" : "text/html; charset=utf-8");
        const contentDisposition = upstream.headers.get("content-disposition") ??
          `attachment; filename="atlas-report-${req.params.reportId.slice(0, 8)}.${format}"`;
        const bytes = Buffer.from(await upstream.arrayBuffer());
        return reply
          .header("Content-Type", contentType)
          .header("Content-Disposition", contentDisposition)
          .send(bytes);
      } catch (err) { return handleUpstreamError(err, reply); }
    },
  );

  // ── Document routes ───────────────────────────────────────────────────────

  // GET /api/documents (list)
  app.get(
    "/api/documents",
    async (req: FastifyRequest<{ Querystring: Record<string, string> }>, reply: FastifyReply) => {
      const user = await requireSession(req, reply);
      if (!user) return;
      try {
        return reply.send(await atlasRequest(upstreamUrl("/api/v1/documents", req), user));
      } catch (err) { return handleUpstreamError(err, reply); }
    },
  );

  // GET /api/documents/:id/text
  app.get(
    "/api/documents/:id/text",
    async (req: FastifyRequest<{ Params: { id: string } }>, reply: FastifyReply) => {
      const user = await requireSession(req, reply);
      if (!user) return;
      try {
        return reply.send(await atlasRequest(`/api/v1/documents/${req.params.id}/text`, user));
      } catch (err) { return handleUpstreamError(err, reply); }
    },
  );

  // POST /api/documents/:id/ingest
  app.post(
    "/api/documents/:id/ingest",
    async (req: FastifyRequest<{ Params: { id: string }; Querystring: Record<string, string> }>, reply: FastifyReply) => {
      const user = await requireSession(req, reply);
      if (!user) return;
      if (!checkCsrf(req, reply)) return;
      const ip = req.ip;
      const rl = checkRateLimit(ip, 30, 60000);
      if (!rl.allowed) {
        return reply.status(429).header("Retry-After", String(Math.ceil(rl.retryAfterMs / 1000))).send({ error: "Too many requests" });
      }
      try {
        const data = await atlasRequest(upstreamUrl(`/api/v1/documents/${req.params.id}/ingest`, req), user, { method: "POST" });
        return reply.send(data);
      } catch (err) { return handleUpstreamError(err, reply); }
    },
  );

  // ── Admin routes ──────────────────────────────────────────────────────────────

  // POST /api/admin/legal-holds — apply a legal hold (admin + MFA required upstream)
  app.post(
    "/api/admin/legal-holds",
    async (req: FastifyRequest<{ Body: unknown }>, reply: FastifyReply) => {
      const user = await requireSession(req, reply);
      if (!user) return;
      if (!checkCsrf(req, reply)) return;
      const ip = req.ip;
      // Tight rate limit: legal-hold operations are infrequent; 10/min per IP
      const rl = checkRateLimit(ip, 10, 60_000);
      if (!rl.allowed) {
        return reply
          .status(429)
          .header("Retry-After", String(Math.ceil(rl.retryAfterMs / 1000)))
          .send({ error: "Too many requests" });
      }
      const mfaCode = req.headers["x-mfa-code"];
      try {
        const data = await atlasRequest(
          "/api/v1/admin/legal-holds",
          user,
          {
            method: "POST",
            body: JSON.stringify(req.body),
            headers: mfaCode ? { "x-mfa-code": String(mfaCode) } : {},
          },
        );
        return reply.status(201).send(data);
      } catch (err) { return handleUpstreamError(err, reply); }
    },
  );

  // DELETE /api/admin/legal-holds — release a legal hold (admin + MFA required upstream)
  app.delete(
    "/api/admin/legal-holds",
    async (req: FastifyRequest<{ Body: unknown }>, reply: FastifyReply) => {
      const user = await requireSession(req, reply);
      if (!user) return;
      if (!checkCsrf(req, reply)) return;
      const ip = req.ip;
      const rl = checkRateLimit(ip, 10, 60_000);
      if (!rl.allowed) {
        return reply
          .status(429)
          .header("Retry-After", String(Math.ceil(rl.retryAfterMs / 1000)))
          .send({ error: "Too many requests" });
      }
      const mfaCode = req.headers["x-mfa-code"];
      try {
        const data = await atlasRequest(
          "/api/v1/admin/legal-holds",
          user,
          {
            method: "DELETE",
            body: JSON.stringify(req.body),
            headers: mfaCode ? { "x-mfa-code": String(mfaCode) } : {},
          },
        );
        return reply.send(data);
      } catch (err) { return handleUpstreamError(err, reply); }
    },
  );
}
