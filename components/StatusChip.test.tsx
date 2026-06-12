/**
 * Unit tests: StatusChip, ConfidenceChip, ReliabilityChip.
 *
 * File location: apps/web/src/components/StatusChip.test.tsx
 *
 * Run with:
 *   npx vitest run src/components/StatusChip.test.tsx
 */

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  StatusChip,
  ConfidenceChip,
  ReliabilityChip,
} from "./StatusChip";

// ── StatusChip ────────────────────────────────────────────────────────────────

describe("StatusChip", () => {
  it("renders 'Disputed' for OPEN status", () => {
    render(<StatusChip status="OPEN" />);
    expect(screen.getByText("Disputed")).toBeTruthy();
  });

  it("renders 'Resolved' for RESOLVED status", () => {
    render(<StatusChip status="RESOLVED" />);
    expect(screen.getByText("Resolved")).toBeTruthy();
  });

  it("renders 'Raw claim' for RAW status", () => {
    render(<StatusChip status="RAW" />);
    expect(screen.getByText("Raw claim")).toBeTruthy();
  });

  it("renders 'Confirmed' for CONFIRMED status", () => {
    render(<StatusChip status="CONFIRMED" />);
    expect(screen.getByText("Confirmed")).toBeTruthy();
  });

  it("renders 'Manual override' for MANUAL_OVERRIDE", () => {
    render(<StatusChip status="MANUAL_OVERRIDE" />);
    expect(screen.getByText("Manual override")).toBeTruthy();
  });

  it("renders 'Superseded' for SUPERSEDED", () => {
    render(<StatusChip status="SUPERSEDED" />);
    expect(screen.getByText("Superseded")).toBeTruthy();
  });

  it("falls back to the raw status string for an unknown value", () => {
    render(<StatusChip status="SOME_UNKNOWN_STATUS" />);
    expect(screen.getByText("SOME_UNKNOWN_STATUS")).toBeTruthy();
  });

  it("applies an extra className when provided", () => {
    const { container } = render(
      <StatusChip status="OPEN" className="ml-2 test-extra" />,
    );
    expect(container.firstElementChild?.className).toContain("test-extra");
  });

  it("applies chip-disputed class for OPEN", () => {
    const { container } = render(<StatusChip status="OPEN" />);
    expect(container.firstElementChild?.className).toContain("chip-disputed");
  });

  it("applies chip-resolved class for RESOLVED", () => {
    const { container } = render(<StatusChip status="RESOLVED" />);
    expect(container.firstElementChild?.className).toContain("chip-resolved");
  });
});

// ── ConfidenceChip ────────────────────────────────────────────────────────────

describe("ConfidenceChip", () => {
  it("renders 'Well sourced' for high confidence", () => {
    render(<ConfidenceChip confidence="high" />);
    expect(screen.getByText("Well sourced")).toBeTruthy();
  });

  it("renders 'Mostly sourced' for medium confidence", () => {
    render(<ConfidenceChip confidence="medium" />);
    expect(screen.getByText("Mostly sourced")).toBeTruthy();
  });

  it("renders 'Partially sourced' for low confidence", () => {
    render(<ConfidenceChip confidence="low" />);
    expect(screen.getByText("Partially sourced")).toBeTruthy();
  });

  it("renders 'Weakly sourced' for unknown confidence", () => {
    render(<ConfidenceChip confidence="unknown" />);
    expect(screen.getByText("Weakly sourced")).toBeTruthy();
  });

  it("falls back gracefully for an unrecognised band", () => {
    render(<ConfidenceChip confidence="very_high" />);
    // Falls back to the raw band name
    expect(screen.getByText("very_high")).toBeTruthy();
  });

  it("never renders a percentage or causal probability number", () => {
    render(<ConfidenceChip confidence="high" />);
    // The spec requires evidence-support language only — no raw numeric probabilities.
    expect(screen.queryByText(/\d+%/)).toBeNull();
  });
});

// ── ReliabilityChip ───────────────────────────────────────────────────────────

describe("ReliabilityChip", () => {
  it.each([1, 2, 3, 4, 5])("renders 'Tier %i' for tier %i", (tier) => {
    render(<ReliabilityChip tier={tier} />);
    expect(screen.getByText(`Tier ${tier}`)).toBeTruthy();
  });

  it("renders a fallback for tier 0 (out-of-range)", () => {
    const { container } = render(<ReliabilityChip tier={0} />);
    // Should render something — not crash.
    expect(container.firstElementChild).toBeTruthy();
  });

  it("renders a fallback for tier 99 (out-of-range)", () => {
    render(<ReliabilityChip tier={99} />);
    expect(screen.getByText("Tier 99")).toBeTruthy();
  });

  it("applies a resolved-style class for Tier 1 (most trusted)", () => {
    const { container } = render(<ReliabilityChip tier={1} />);
    expect(container.firstElementChild?.className).toContain("resolved");
  });
});
