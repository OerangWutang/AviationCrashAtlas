/**
 * In-memory login rate limiter.
 *
 * Tracks failed login attempts per IP address within a sliding window.
 * On exceeded limit, returns the remaining wait time in milliseconds.
 *
 * This is intentionally simple (in-process, single-node). For multi-process
 * deployments, replace with a Redis-backed counter. For MVP this is sufficient.
 */

interface RateLimitEntry {
  attempts: number;
  windowStart: number;
}

// Separate stores so login-limiter and general-route-limiter counters never collide.
const _loginStore = new Map<string, RateLimitEntry>();
const _store = new Map<string, RateLimitEntry>();

/** Returns true if the request should be blocked, false if allowed. */
export function checkLoginRateLimit(
  ip: string,
  maxAttempts: number,
  windowMs: number,
): { allowed: boolean; retryAfterMs: number } {
  const now = Date.now();
  const entry = _loginStore.get(ip);

  if (!entry || now - entry.windowStart >= windowMs) {
    // First attempt or window expired — start fresh
    _loginStore.set(ip, { attempts: 1, windowStart: now });
    return { allowed: true, retryAfterMs: 0 };
  }

  if (entry.attempts >= maxAttempts) {
    const retryAfterMs = windowMs - (now - entry.windowStart);
    return { allowed: false, retryAfterMs };
  }

  entry.attempts += 1;
  return { allowed: true, retryAfterMs: 0 };
}

/** Record a failed attempt (call after verifying credentials failed). */
export function recordFailedLogin(
  ip: string,
  maxAttempts: number,
  windowMs: number,
): void {
  const now = Date.now();
  const entry = _loginStore.get(ip);

  if (!entry || now - entry.windowStart >= windowMs) {
    _loginStore.set(ip, { attempts: 1, windowStart: now });
    return;
  }

  // Cap at maxAttempts so integer doesn't grow unboundedly
  entry.attempts = Math.min(entry.attempts + 1, maxAttempts);
}

/** Clear all rate-limit state (tests only). */
export function _clearRateLimits(): void {
  _loginStore.clear();
  _store.clear();
}

/** Periodic cleanup of expired login-limiter windows (call from a setInterval). */
export function sweepLoginEntries(windowMs: number): void {
  const now = Date.now();
  for (const [ip, entry] of _loginStore.entries()) {
    if (now - entry.windowStart >= windowMs) {
      _loginStore.delete(ip);
    }
  }
}

/** Periodic cleanup of expired general rate-limit windows (call from a setInterval). */
export function sweepExpiredEntries(windowMs: number): void {
  const now = Date.now();
  for (const [ip, entry] of _store.entries()) {
    if (now - entry.windowStart >= windowMs) {
      _store.delete(ip);
    }
  }
}

/**
 * Generic per-IP rate limiter for any mutating route.
 * Pre-increments on every call. Use a separate store from the login limiter
 * so document-upload or conflict-resolve quota never bleeds into login checks.
 */
export function checkRateLimit(
  ip: string,
  maxRequests: number,
  windowMs: number,
): { allowed: boolean; retryAfterMs: number } {
  const now = Date.now();
  const entry = _store.get(ip);

  if (!entry || now - entry.windowStart >= windowMs) {
    _store.set(ip, { attempts: 1, windowStart: now });
    return { allowed: true, retryAfterMs: 0 };
  }

  if (entry.attempts >= maxRequests) {
    const retryAfterMs = windowMs - (now - entry.windowStart);
    return { allowed: false, retryAfterMs };
  }

  entry.attempts += 1;
  return { allowed: true, retryAfterMs: 0 };
}
