import { apiFetch } from "../../lib/api";
import type { CurrentUser } from "../../types/api";

export interface LoginPayload {
  email: string;
  password: string;
}

export async function login(payload: LoginPayload): Promise<CurrentUser> {
  // BFF owns auth — these endpoints never hit FastAPI directly.
  // The BFF sets an HttpOnly session cookie; no API key is ever sent to the browser.
  return apiFetch<CurrentUser>("/auth/login", {
    method: "POST",
    body: JSON.stringify(payload),
    snakeBody: false,
  });
}

export async function logout(): Promise<void> {
  // Logout requires the CSRF token header (it's a mutation on the BFF session).
  const csrfToken = getCsrfToken();
  await apiFetch<void>("/auth/logout", {
    method: "POST",
    snakeBody: false,
    headers: csrfToken ? { "X-CSRF-Token": csrfToken } : {},
  });
}

export async function getCurrentUser(): Promise<CurrentUser> {
  return apiFetch<CurrentUser>("/auth/me");
}

/**
 * Read the CSRF token from the atlas-csrf cookie.
 * The BFF sets this as a non-HttpOnly cookie on login and /auth/me so the SPA
 * can read it and send it as a header on state-mutating requests.
 */
export function getCsrfToken(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(/(?:^|;\s*)atlas-csrf=([^;]+)/);
  return match?.[1] ? decodeURIComponent(match[1]) : null;
}
