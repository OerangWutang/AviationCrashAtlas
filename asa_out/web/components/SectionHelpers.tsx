/**
 * Shared layout primitives for AccidentDetailPanel sub-sections.
 *
 * Extracted so that DisputedSection, TimelineSection, and SourcesSection
 * can all import from one place rather than duplicating the markup.
 */
import type { ReactNode } from "react";

export function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <h3
      className="mb-4 border-b border-stone-100 pb-2 text-[11px] uppercase tracking-[0.16em] text-stone-400"
      style={{ fontFamily: "var(--ff-mono)" }}
    >
      {children}
    </h3>
  );
}

export function Chip({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center rounded-full border border-stone-200 bg-stone-50 px-2.5 py-1 text-[11px] leading-none text-stone-500">
      {children}
    </span>
  );
}
