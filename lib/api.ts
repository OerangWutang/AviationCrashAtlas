/**
 * API client — the ONLY module that talks to the server.
 *
 * Points at the BFF (services/bff), which owns sessions and proxies to FastAPI.
 * Uses credentials: "include" so the HttpOnly session cookie is sent.
 * Injects the X-CSRF-Token header on mutating requests (POST/PUT/PATCH/DELETE).
 * Converts snake_case responses to camelCase for the frontend.
 *
 * The browser NEVER sends X-API-Key. That key lives server-side in the BFF.
 */

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

export interface ApiError {
  status: number;
  message: string;
  detail?: unknown;
}

export function isApiError(err: unknown): err is ApiError {
  return (
    typeof err === "object" &&
    err !== null &&
    "status" in err &&
    typeof (err as ApiError).status === "number"
  );
}

/** Returns true for a 409 conflict-modified response. */
export function isConflictModified(err: unknown): boolean {
  return isApiError(err) && err.status === 409;
}

/** Returns true for a 403 forbidden response. */
export function isForbidden(err: unknown): boolean {
  return isApiError(err) && err.status === 403;
}

/** Returns true for a 5xx server error. */
export function isServerError(err: unknown): boolean {
  return isApiError(err) && err.status >= 500 && err.status < 600;
}

// ── snake_case → camelCase transformer ──────────────────────────────────────

function snakeToCamel(s: string): string {
  return s.replace(/_([a-z0-9])/g, (_, c) => c.toUpperCase());
}

function camelizeKeys(obj: unknown): unknown {
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

// ── camelCase → snake_case for request bodies ───────────────────────────────

function camelToSnake(s: string): string {
  return s.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`);
}

function snakeizeKeys(obj: unknown): unknown {
  if (Array.isArray(obj)) return obj.map(snakeizeKeys);
  if (obj !== null && typeof obj === "object" && !(obj instanceof Date)) {
    return Object.fromEntries(
      Object.entries(obj as Record<string, unknown>).map(([k, v]) => [
        camelToSnake(k),
        snakeizeKeys(v),
      ]),
    );
  }
  return obj;
}

// ── CSRF token ──────────────────────────────────────────────────────────────

const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/**
 * Read the CSRF token from the atlas-csrf cookie (set by the BFF on login
 * and /auth/me as a non-HttpOnly cookie so JS can read it).
 */
function readCsrfToken(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(/(?:^|;\s*)atlas-csrf=([^;]+)/);
  return match?.[1] ? decodeURIComponent(match[1]) : null;
}

// ── Core fetch ──────────────────────────────────────────────────────────────

export async function apiFetch<T>(
  path: string,
  options: RequestInit & { skipCamelize?: boolean; snakeBody?: boolean } = {},
): Promise<T> {
  const { skipCamelize, snakeBody, ...fetchOptions } = options;

  // If body is provided and snakeBody isn't explicitly false, snake_case it
  let body = fetchOptions.body;
  if (body && typeof body === "string" && snakeBody !== false) {
    try {
      const parsed = JSON.parse(body);
      body = JSON.stringify(snakeizeKeys(parsed));
    } catch {
      // not JSON, pass through
    }
  }

  const method = (fetchOptions.method ?? "GET").toUpperCase();
  const csrfToken = MUTATING_METHODS.has(method) ? readCsrfToken() : null;

  const response = await fetch(`${API_BASE}${path}`, {
    ...fetchOptions,
    body,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...((fetchOptions.headers as Record<string, string>) ?? {}),
      ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
    },
  });

  if (!response.ok) {
    let detail: unknown = null;
    try {
      const raw = await response.json();
      detail = camelizeKeys(raw);
    } catch {
      detail = await response.text().catch(() => null);
    }

    let message: string;
    if (response.status === 403) {
      message = "You don't have permission to perform this action. Your session may have expired.";
    } else if (response.status >= 500) {
      message = "Server error. Please try again later.";
    } else {
      message = `Request failed with status ${response.status}`;
    }

    throw {
      status: response.status,
      message,
      detail,
    } satisfies ApiError;
  }

  if (response.status === 204) return undefined as T;

  const raw = await response.json();
  return (skipCamelize ? raw : camelizeKeys(raw)) as T;
}
