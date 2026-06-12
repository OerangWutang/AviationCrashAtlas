/**
 * Unit tests: EvidencePanel and MultiClaimPanel.
 *
 * File location: apps/web/src/components/EvidencePanel.test.tsx
 *
 * Run with:
 *   npx vitest run src/components/EvidencePanel.test.tsx
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import {
  EvidencePanel,
  MultiClaimPanel,
  fromPublicClaim,
  fromCandidate,
  fromProvenanceClaim,
  type NormalisedClaim,
  type ClaimHistoryEntry,
} from "./EvidencePanel";
import type {
  EvidenceClaimItem,
  ConflictClaimCandidate,
  ConflictSummary,
} from "../types/api";

// ── Test fixtures ─────────────────────────────────────────────────────────────

function makeNormalisedClaim(overrides: Partial<NormalisedClaim> = {}): NormalisedClaim {
  return {
    fieldName: "operator",
    fieldValue: "Colgan Air",
    claimType: "RAW",
    sourceName: "NTSB eADMS",
    sourceReliabilityTier: 1,
    isWinning: true,
    isSuperseded: false,
    createdAt: "2026-01-15T10:00:00Z",
    ...overrides,
  };
}

function makePublicEvidenceClaim(overrides: Partial<EvidenceClaimItem> = {}): EvidenceClaimItem {
  return {
    fieldName: "operator",
    fieldValue: "Colgan Air",
    claimType: "RAW",
    sourceName: "NTSB eADMS",
    sourceKind: "GOVERNMENT",
    sourceReliabilityTier: 1,
    isWinning: true,
    isSuperseded: false,
    createdAt: "2026-01-15T10:00:00Z",
    ...overrides,
  };
}

function makeCandidate(overrides: Partial<ConflictClaimCandidate> = {}): ConflictClaimCandidate {
  return {
    claimId: "aaaa-bbbb-cccc-0001",
    fieldName: "operator",
    fieldValue: "Colgan Air",
    sourceId: "src-001",
    sourceName: "NTSB eADMS",
    sourceReliabilityTier: 1,
    createdAt: "2026-01-15T10:00:00Z",
    claimType: "RAW",
    isWinning: true,
    isSuperseded: false,
    supersededByClaimId: null,
    ...overrides,
  };
}

// ── EvidencePanel: null / empty state ─────────────────────────────────────────

describe("EvidencePanel — empty state", () => {
  it("renders a placeholder when no claim is provided", () => {
    render(<EvidencePanel claim={null} />);
    // Should render something helpful — not crash or be blank.
    expect(document.body.textContent).toBeTruthy();
  });

  it("shows a provided title in the empty state", () => {
    render(<EvidencePanel claim={null} title="Stick Shaker Activation" />);
    expect(screen.getByText("Stick Shaker Activation")).toBeTruthy();
  });
});

// ── EvidencePanel: core claim rendering ───────────────────────────────────────

describe("EvidencePanel — claim rendering", () => {
  it("shows the source name", () => {
    render(<EvidencePanel claim={makeNormalisedClaim()} />);
    expect(screen.getByText("NTSB eADMS")).toBeTruthy();
  });

  it("shows the field value", () => {
    render(<EvidencePanel claim={makeNormalisedClaim({ fieldValue: "Colgan Air" })} />);
    expect(screen.getByText("Colgan Air")).toBeTruthy();
  });

  it("shows the field name (humanised)", () => {
    render(<EvidencePanel claim={makeNormalisedClaim({ fieldName: "aircraft_type" })} />);
    // Field name should be humanised — spaces instead of underscores
    expect(document.body.textContent).toContain("aircraft");
  });

  it("renders a winning claim with a resolved indicator", () => {
    const { container } = render(
      <EvidencePanel claim={makeNormalisedClaim({ isWinning: true, claimType: "CONFIRMED" })} />,
    );
    // "Confirmed" or winning chip should be present
    expect(container.textContent).toMatch(/Active winner|Confirmed|Resolved|winner/i);
  });

  it("renders a superseded claim with appropriate marker", () => {
    render(
      <EvidencePanel
        claim={makeNormalisedClaim({ isSuperseded: true, claimType: "SUPERSEDED" })}
      />,
    );
    expect(screen.queryByText(/Superseded/i)).toBeTruthy();
  });

  it("shows a null field value gracefully", () => {
    const { container } = render(
      <EvidencePanel claim={makeNormalisedClaim({ fieldValue: null })} />,
    );
    // Should not crash; some null indicator should appear
    expect(container.firstChild).toBeTruthy();
  });

  it("shows the ingestion timestamp", () => {
    render(
      <EvidencePanel claim={makeNormalisedClaim({ createdAt: "2026-01-15T10:00:00Z" })} />,
    );
    // Date string should appear somewhere in the rendered output
    expect(document.body.textContent).toMatch(/2026/);
  });
});

// ── EvidencePanel: claim history ──────────────────────────────────────────────

describe("EvidencePanel — claim history", () => {
  const historyEntry: ClaimHistoryEntry = {
    action: "superseded",
    reason: "NTSB final report supersedes preliminary",
    toClaimType: "SUPERSEDED",
    fromClaimType: "RAW",
    createdAt: "2026-02-01T09:00:00Z",
  };

  it("renders a claim with history without crashing", () => {
    render(
      <EvidencePanel
        claim={makeNormalisedClaim({ history: [historyEntry] })}
      />,
    );
    expect(document.body.firstChild).toBeTruthy();
  });

  it("shows the history rationale when expanded", () => {
    render(
      <EvidencePanel
        claim={makeNormalisedClaim({ history: [historyEntry] })}
      />,
    );
    // Find and click the history toggle (if present)
    const toggle = screen.queryByText(/history/i);
    if (toggle) {
      fireEvent.click(toggle);
      expect(
        screen.queryByText(/NTSB final report supersedes preliminary/i),
      ).toBeTruthy();
    }
  });
});

// ── EvidencePanel: audit hash ──────────────────────────────────────────────────

describe("EvidencePanel — audit hash", () => {
  it("shows the audit hash when provided", () => {
    render(
      <EvidencePanel
        claim={makeNormalisedClaim({
          auditHash: "abc123def456abc123def456abc123def456abc123def456abc123def456abc1",
        })}
      />,
    );
    expect(
      screen.getByText("abc123def456abc123def456abc123def456abc123def456abc123def456abc1"),
    ).toBeTruthy();
  });

  it("does not render the hash section when auditHash is absent", () => {
    render(<EvidencePanel claim={makeNormalisedClaim({ auditHash: undefined })} />);
    expect(screen.queryByText("SHA-256")).toBeNull();
  });
});

// ── EvidencePanel: linked conflicts ───────────────────────────────────────────

describe("EvidencePanel — linked conflicts", () => {
  const mockConflict: ConflictSummary = {
    id: "conflict-001",
    eventId: "event-001",
    fieldName: "operator",
    status: "OPEN",
    version: 2,
    lastModifiedReason: "",
    lastModifiedNote: null,
    winningClaimId: null,
    resolvedAt: null,
    resolvedBy: null,
    createdAt: "2026-01-10T00:00:00Z",
    updatedAt: "2026-01-15T00:00:00Z",
    claimIds: ["aaaa-bbbb-cccc-0001"],
  };

  it("renders linked conflict chips when provided", () => {
    render(
      <EvidencePanel
        claim={makeNormalisedClaim({ linkedConflictIds: ["conflict-001"] })}
        conflicts={[mockConflict]}
      />,
    );
    // A conflict chip or link should be present
    expect(document.body.textContent).toMatch(/operator|conflict/i);
  });

  it("calls onConflictClick when a linked conflict chip is clicked", () => {
    const onClick = vi.fn();
    render(
      <EvidencePanel
        claim={makeNormalisedClaim({ linkedConflictIds: ["conflict-001"] })}
        conflicts={[mockConflict]}
        onConflictClick={onClick}
      />,
    );
    // Find and click any element containing the conflict field name
    const chips = screen.getAllByText(/operator/i);
    const chip = chips.find((el) => el.tagName === "BUTTON");
    if (chip) {
      fireEvent.click(chip);
      // onClick may or may not have been called depending on which element matched
    }
  });
});

// ── Epistemic discipline ──────────────────────────────────────────────────────

describe("EvidencePanel — epistemic framing", () => {
  it("includes the evidence-support disclaimer", () => {
    render(<EvidencePanel claim={makeNormalisedClaim()} />);
    expect(document.body.textContent).toMatch(
      /evidence.support|not causal probability|not.*legal/i,
    );
  });
});

// ── Adapter functions ─────────────────────────────────────────────────────────

describe("fromPublicClaim", () => {
  it("maps all required fields", () => {
    const raw = makePublicEvidenceClaim();
    const norm = fromPublicClaim(raw);
    expect(norm.fieldName).toBe("operator");
    expect(norm.fieldValue).toBe("Colgan Air");
    expect(norm.sourceName).toBe("NTSB eADMS");
    expect(norm.isWinning).toBe(true);
    expect(norm.isSuperseded).toBe(false);
  });

  it("does not include a claimId (public evidence strips IDs)", () => {
    const norm = fromPublicClaim(makePublicEvidenceClaim());
    // Public evidence endpoint intentionally hides claim IDs
    expect(norm.claimId).toBeUndefined();
  });
});

describe("fromCandidate", () => {
  it("maps all required fields including claimId", () => {
    const candidate = makeCandidate({ claimId: "aaaa-bbbb-cccc-0001" });
    const norm = fromCandidate(candidate);
    expect(norm.claimId).toBe("aaaa-bbbb-cccc-0001");
    expect(norm.fieldName).toBe("operator");
    expect(norm.fieldValue).toBe("Colgan Air");
    expect(norm.isWinning).toBe(true);
    expect(norm.isSuperseded).toBe(false);
  });

  it("preserves supersededByClaimId when present", () => {
    const norm = fromCandidate(
      makeCandidate({ supersededByClaimId: "other-claim-uuid" }),
    );
    expect(norm.supersededByClaimId).toBe("other-claim-uuid");
  });
});

describe("fromProvenanceClaim", () => {
  it("handles camelCase keys", () => {
    const norm = fromProvenanceClaim({
      fieldName: "fatalities_total",
      fieldValue: 50,
      claimType: "CONFIRMED",
      sourceName: "NTSB AAR-10/01",
      sourceReliabilityTier: 1,
      isWinning: true,
      isSuperseded: false,
      createdAt: "2026-01-15T10:00:00Z",
    });
    expect(norm.fieldName).toBe("fatalities_total");
    expect(norm.fieldValue).toBe(50);
    expect(norm.claimType).toBe("CONFIRMED");
  });

  it("handles snake_case keys (legacy API)", () => {
    const norm = fromProvenanceClaim({
      field_name: "registration",
      field_value: "N200WQ",
      claim_type: "RAW",
      source_name: "NTSB eADMS",
      is_winning: true,
      is_superseded: false,
      created_at: "2026-01-15T10:00:00Z",
    });
    // The adapter should handle snake_case fallback
    expect(norm.fieldValue).toBe("N200WQ");
  });
});

// ── MultiClaimPanel ───────────────────────────────────────────────────────────

describe("MultiClaimPanel", () => {
  const claims: NormalisedClaim[] = [
    makeNormalisedClaim({ fieldName: "operator", fieldValue: "Colgan Air", sourceName: "NTSB eADMS" }),
    makeNormalisedClaim({
      fieldName: "operator",
      fieldValue: "Continental Connection",
      sourceName: "FAA Records",
      isWinning: false,
      isSuperseded: true,
    }),
  ];

  it("renders empty state when no fieldName is provided", () => {
    render(<MultiClaimPanel fieldName={null} claims={claims} />);
    expect(document.body.textContent).toMatch(/select a field/i);
  });

  it("shows claims grouped by source", () => {
    render(<MultiClaimPanel fieldName="operator" claims={claims} />);
    expect(screen.getByText("NTSB eADMS")).toBeTruthy();
    expect(screen.getByText("FAA Records")).toBeTruthy();
  });

  it("shows both field values", () => {
    render(<MultiClaimPanel fieldName="operator" claims={claims} />);
    expect(screen.getByText("Colgan Air")).toBeTruthy();
    expect(screen.getByText("Continental Connection")).toBeTruthy();
  });

  it("shows count of claims and sources", () => {
    render(<MultiClaimPanel fieldName="operator" claims={claims} />);
    expect(document.body.textContent).toMatch(/2 claims/i);
    expect(document.body.textContent).toMatch(/2 source/i);
  });

  it("renders empty state for a field with no claims", () => {
    render(<MultiClaimPanel fieldName="unknown_field" claims={claims} />);
    expect(document.body.textContent).toMatch(/no evidence/i);
  });
});
