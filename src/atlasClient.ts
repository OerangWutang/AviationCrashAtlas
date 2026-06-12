/**
 * Server-side Atlas API client.
 *
 * This is the ONLY place in the BFF that holds or uses the Atlas API key.
 * It attaches X-API-Key to every upstream request and strips it from
 * every response. The browser never sees the key.
 *
 * Uses Node's built-in `fetch` (Node 18+) / undici for performance.
 */

import { fetch as undiciFetch, type RequestInit } from "undici";
import { getConfig } from "./config.js";
import { decryptApiKey, encryptApiKey } from "./auth/crypto.js";
import type { SessionUser } from "./auth/sessions.js";

export class AtlasUpstreamError extends Error {
  constructor(
    public readonly status: number,
    public readonly body: unknown,
  ) {
    super(`Atlas API responded with ${status}`);
    this.name = "AtlasUpstreamError";
  }
}

/** snake_case → camelCase key transformer (mirrors apps/web/src/lib/api.ts). */
function snakeToCamel(s: string): string {
  return s.replace(/_([a-z0-9])/g, (_, c: string) => c.toUpperCase());
}

export function camelizeKeys(obj: unknown): unknown {
  if (Array.isArray(obj)) return obj.map(camelizeKeys);
  if (obj !== null && typeof obj === "object" && !(obj instanceof Date)) {
    return Object.fromEntries(
      Object.entries(obj as Record<string, unknown>).map(([k, v]) => [
        snakeToCamel(k),
        camelizeKeys(v),
      ]),
    );
  }
  return obj;
}

/**
 * Make an authenticated request to the Atlas FastAPI backend.
 *
 * @param path    Path relative to ATLAS_API_BASE_URL, e.g. /api/v1/public/events
 * @param user    Resolved session user (provides the encrypted API key)
 * @param init    Optional fetch init (method, body, headers)
 * @returns       Camel-cased parsed JSON response
 */
export async function atlasRequest<T = unknown>(
  path: string,
  user: SessionUser | { encryptedApiKey: string },
  init: RequestInit = {},
): Promise<T> {
  const config = getConfig();

  // Decrypt the user's Atlas API key server-side
  const apiKey = decryptApiKey(user.encryptedApiKey, config.sessionSecret);

  const url = `${config.atlasApiBaseUrl}${path}`;

  const response = await undiciFetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...((init.headers as Record<string, string>) ?? {}),
      // This is the security-critical line: key injected here, never forwarded
      "X-API-Key": apiKey,
    },
  });

  if (!response.ok) {
    let body: unknown = null;
    try {
      body = camelizeKeys(await response.json());
    } catch {
      body = await response.text().catch(() => null);
    }
    throw new AtlasUpstreamError(response.status, body);
  }

  if (response.status === 204) return undefined as T;

  const raw = await response.json();
  return camelizeKeys(raw) as T;
}

/**
 * atlasRequest using the seed API key (for bootstrap/dev scenarios where
 * no user session exists). Used by health-check and seed-data paths only.
 */
export async function atlasSeedRequest<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const config = getConfig();

  if (!config.atlasSeedApiKey) {
    throw new Error("ATLAS_SEED_API_KEY is not configured");
  }

  const encryptedApiKey = encryptApiKey(config.atlasSeedApiKey, config.sessionSecret);

  return atlasRequest<T>(path, { encryptedApiKey }, init);
}
