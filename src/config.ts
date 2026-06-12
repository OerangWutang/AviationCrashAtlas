/**
 * Environment configuration for the BFF.
 *
 * All values are read once at startup. Missing required values throw
 * immediately so the process exits clearly rather than failing mid-request.
 */

import { fileURLToPath } from "node:url";
import path from "node:path";

// Project root directory — resolves the server file's location to get
// the package root regardless of the working directory at startup.
const _projectRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);

export interface BffConfig {
  /** Port to listen on. Default 3001. */
  port: number;
  /** Host to bind. Default 0.0.0.0 */
  host: string;
  /** The origin of the apps/web SPA — used for CORS and CSRF checks. */
  webOrigin: string;
  /** Internal base URL of the Atlas FastAPI service. Never leaves the server. */
  atlasApiBaseUrl: string;
  /**
   * The raw Atlas API key used by the BFF to proxy requests.
   * In production each human user gets their own provisioned key;
   * this is the seed/dev key used before per-user key provisioning is wired.
   */
  atlasSeedApiKey: string;
  /** SQLite file path (or :memory: for tests). */
  dbPath: string;
  /** Secret for signing session tokens. Min 32 chars in production. */
  sessionSecret: string;
  /** Session cookie name. */
  sessionCookieName: string;
  /** Session TTL in seconds. Default 8 hours. */
  sessionTtlSeconds: number;
  /** Whether the app is running in production mode. */
  isProduction: boolean;
  /** Max login attempts per IP per window before throttling. */
  loginRateLimit: number;
  /** Login rate-limit window in milliseconds. */
  loginRateLimitWindowMs: number;
  /** Redis URL for rate limiting and session cache. Optional. */
  redisUrl: string | null;
}

function required(name: string): string {
  const v = process.env[name];
  if (!v) throw new Error(`Missing required environment variable: ${name}`);
  return v;
}

function optional(name: string, fallback: string): string {
  return process.env[name] ?? fallback;
}

function optionalInt(name: string, fallback: number): number {
  const v = process.env[name];
  if (!v) return fallback;
  const n = parseInt(v, 10);
  if (isNaN(n)) throw new Error(`${name} must be an integer, got: ${v}`);
  return n;
}

let _config: BffConfig | null = null;

export function getConfig(): BffConfig {
  if (_config) return _config;

  const isProduction = optional("NODE_ENV", "development") === "production";

  const sessionSecret = optional("BFF_SESSION_SECRET", "dev-secret-change-in-production-32chars");
  if (isProduction && sessionSecret.length < 32) {
    throw new Error("BFF_SESSION_SECRET must be at least 32 characters in production");
  }

  _config = {
    port: optionalInt("BFF_PORT", 3001),
    host: optional("BFF_HOST", "0.0.0.0"),
    webOrigin: optional("WEB_ORIGIN", "http://localhost:5173"),
    atlasApiBaseUrl: optional("ATLAS_API_BASE_URL", "http://localhost:8000"),
    atlasSeedApiKey: optional("ATLAS_SEED_API_KEY", ""),
    dbPath: path.resolve(_projectRoot, optional("BFF_DB_PATH", "./bff.db")),
    sessionSecret,
    sessionCookieName: optional("BFF_SESSION_COOKIE", "__Host-atlas-session"),
    sessionTtlSeconds: optionalInt("BFF_SESSION_TTL_SECONDS", 8 * 60 * 60),
    isProduction,
    loginRateLimit: optionalInt("LOGIN_RATE_LIMIT", 10),
    loginRateLimitWindowMs: optionalInt("LOGIN_RATE_LIMIT_WINDOW_MS", 15 * 60 * 1000),
    redisUrl: optional("REDIS_URL", "") || null,
  };

  return _config;
}

/** Reset config (tests only). */
export function _resetConfig(): void {
  _config = null;
}
