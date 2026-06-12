/**
 * CSRF protection via the double-submit cookie pattern.
 *
 * On GET /auth/me we issue a CSRF token as a non-HttpOnly cookie.
 * On every state-mutating request (POST/PUT/PATCH/DELETE) the client must
 * echo it back as the X-CSRF-Token header. Because the attacker can't read
 * the cookie value (cross-origin reads are blocked by SameSite=Lax + the
 * browser same-origin policy), they can't forge the header.
 *
 * SameSite=Lax already prevents most CSRF with a modern browser, but the
 * double-submit gives defence-in-depth and satisfies enterprise security
 * questionnaires that require an explicit anti-CSRF mechanism.
 */

import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { getConfig } from "../config.js";

const CSRF_COOKIE = "atlas-csrf";
const CSRF_HEADER = "x-csrf-token";
const TOKEN_BYTES = 24;

export { CSRF_COOKIE, CSRF_HEADER };

/**
 * Generate a new CSRF token string.
 * The raw random bytes are HMAC-signed with the session secret so we can
 * verify they weren't tampered with (even though the double-submit pattern
 * doesn't strictly require this, it adds robustness).
 */
export function generateCsrfToken(): string {
  const raw = randomBytes(TOKEN_BYTES).toString("hex");
  const { sessionSecret } = getConfig();
  const sig = createHmac("sha256", sessionSecret).update(raw).digest("hex");
  return `${raw}.${sig}`;
}

/**
 * Verify the CSRF token from the request header against the cookie.
 *
 * Two checks in order:
 *   1. HMAC signature — proves the cookie was issued by this server, not
 *      injected via subdomain cookie manipulation or XSS.
 *   2. Double-submit — cookie value must match the header value, proving
 *      the requester could read the cookie (same-origin only).
 */
export function verifyCsrfToken(cookieToken: string | undefined, headerToken: string | undefined): boolean {
  if (!cookieToken || !headerToken) return false;

  // Verify HMAC signature embedded in the cookie token
  const dotIdx = cookieToken.lastIndexOf(".");
  if (dotIdx === -1) return false;
  const raw = cookieToken.slice(0, dotIdx);
  const storedSig = cookieToken.slice(dotIdx + 1);
  const { sessionSecret } = getConfig();
  const expectedSig = createHmac("sha256", sessionSecret).update(raw).digest("hex");
  try {
    if (
      storedSig.length !== expectedSig.length ||
      !timingSafeEqual(Buffer.from(storedSig, "utf8"), Buffer.from(expectedSig, "utf8"))
    ) return false;
  } catch {
    return false;
  }

  // Double-submit: cookie must match header
  if (cookieToken.length !== headerToken.length) return false;
  try {
    return timingSafeEqual(
      Buffer.from(cookieToken, "utf8"),
      Buffer.from(headerToken, "utf8"),
    );
  } catch {
    return false;
  }
}

export function csrfCookieOptions(isProduction: boolean) {
  return {
    httpOnly: false, // intentionally readable by JS so the SPA can send it as a header
    secure: isProduction,
    sameSite: "lax" as const,
    path: "/",
    maxAge: 8 * 60 * 60, // same lifetime as session
  };
}
