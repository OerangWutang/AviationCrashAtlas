/**
 * Vitest setup for apps/web component tests.
 *
 * File location: apps/web/src/test/setup.ts
 *
 * Imported by vitest via `setupFiles` in vite.config.ts.
 *
 * What this does:
 *   1. Imports @testing-library/jest-dom matchers so .toBeInTheDocument()
 *      and friends work in Vitest.
 *   2. Stubs browser APIs that Vitest's jsdom doesn't implement
 *      (clipboard, matchMedia, ResizeObserver).
 *   3. Resets all mocks between tests so state doesn't leak.
 */

import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

// Clean up after each test — removes rendered components and event listeners.
afterEach(() => {
  cleanup();
});

// ── Browser API stubs (jsdom gaps) ────────────────────────────────────────────

// Clipboard API — used by the CopyableHash component in EvidencePanel.
Object.assign(navigator, {
  clipboard: {
    writeText: vi.fn().mockResolvedValue(undefined),
    readText: vi.fn().mockResolvedValue(""),
  },
});

// matchMedia — Tailwind responsive utilities read this.
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

// ResizeObserver — used by some layout components.
globalThis.ResizeObserver = vi.fn().mockImplementation(() => ({
  observe: vi.fn(),
  unobserve: vi.fn(),
  disconnect: vi.fn(),
}));

// CSS.escape — used by the Timeline keyboard navigation scrollIntoView.
if (!globalThis.CSS) {
  // @ts-expect-error — jsdom may not expose CSS global
  globalThis.CSS = { escape: (s: string) => s.replace(/[^\w-]/g, "\\$&") };
}
