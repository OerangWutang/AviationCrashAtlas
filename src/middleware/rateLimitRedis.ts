/**
 * Redis-backed rate limiter for the BFF.
 *
 * Uses a sliding window algorithm with Redis sorted sets.  Each IP address
 * gets a sorted set keyed by `ratelimit:<windowMs>:<ip>` where the set
 * members are request timestamps (milliseconds).  Old entries are trimmed
 * on every write so the set stays bounded to the window size.
 *
 * Falls back to the in-process limiter when Redis is not configured — the
 * BFF is always functional even without Redis (MVP compatible).
 */

import Redis from "ioredis";

let _redis: Redis | null = null;
let _fallback = new Map<string, { attempts: number; windowStart: number }>();

export interface RateLimitResult {
  allowed: boolean;
  retryAfterMs: number;
}

/**
 * Initialise the Redis connection.  Call once at server startup.
 * Returns `false` if Redis is not configured (caller should use in-process).
 */
export async function initRedisRateLimiter(redisUrl?: string): Promise<boolean> {
  if (!redisUrl) return false;
  try {
    _redis = new Redis(redisUrl, {
      maxRetriesPerRequest: 3,
      retryStrategy: (times) => (times > 3 ? null : Math.min(times * 100, 2000)),
    });
    await _redis.ping();
    // Fallback store no longer needed — Redis is live
    _fallback.clear();
    return true;
  } catch {
    _redis = null;
    return false;
  }
}

/** Shutdown the Redis connection gracefully. */
export async function shutdownRedisRateLimiter(): Promise<void> {
  if (_redis) {
    await _redis.quit();
    _redis = null;
  }
}

/**
 * Check and increment the rate limit for an IP address.
 *
 * Uses a sliding window: requests older than `windowMs` are trimmed,
 * then we count the remaining entries.  If the count exceeds `maxRequests`
 * the request is denied.
 */
export async function checkRateLimitRedis(
  ip: string,
  maxRequests: number,
  windowMs: number,
): Promise<RateLimitResult> {
  const now = Date.now();

  if (!_redis) {
    // Fallback to in-process
    return checkRateLimitInProcess(ip, maxRequests, windowMs, now);
  }

  const key = `ratelimit:${windowMs}:${ip}`;
  const oldest = now - windowMs;

  try {
    // Remove entries outside the window
    await _redis.zremrangebyscore(key, 0, oldest);
    // Count remaining
    const count = await _redis.zcard(key);

    if (count >= maxRequests) {
      // Get the oldest entry to calculate retry time
      const oldestEntry = await _redis.zrangebyscore(key, oldest, now, "LIMIT", 0, 1);
      const retryAfterMs = oldestEntry.length > 0
        ? windowMs - (now - Number(oldestEntry[0]?.split(":")[0] ?? now))
        : windowMs;
      return { allowed: false, retryAfterMs: Math.max(retryAfterMs, 100) };
    }

    // Add current request
    await _redis.zadd(key, now, `${now}:${Math.random()}`);
    // Set TTL on the key so it self-clears when the window expires
    await _redis.pexpire(key, windowMs);
    return { allowed: true, retryAfterMs: 0 };
  } catch {
    // Redis error — fall back to in-process for this request
    return checkRateLimitInProcess(ip, maxRequests, windowMs, now);
  }
}

/**
 * In-process fallback rate limiter (mirrors the existing checkRateLimit logic).
 */
function checkRateLimitInProcess(
  ip: string,
  maxRequests: number,
  windowMs: number,
  now: number,
): RateLimitResult {
  const entry = _fallback.get(ip);

  if (!entry || now - entry.windowStart >= windowMs) {
    _fallback.set(ip, { attempts: 1, windowStart: now });
    return { allowed: true, retryAfterMs: 0 };
  }

  if (entry.attempts >= maxRequests) {
    return { allowed: false, retryAfterMs: windowMs - (now - entry.windowStart) };
  }

  entry.attempts += 1;
  return { allowed: true, retryAfterMs: 0 };
}

/** Clear all rate-limit state (tests only). */
export function _clearRateLimits(): void {
  _fallback.clear();
  // Can't clear Redis from here without async — test Redis separately
}