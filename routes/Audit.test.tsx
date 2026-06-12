import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Audit from "./Audit";
import type { FieldExplanationResponse, PageAuditResponse } from "../types/api";

const mocks = vi.hoisted(() => ({
  useCaseAudit: vi.fn(),
  useFieldExplanation: vi.fn(),
  refetchAudit: vi.fn(),
  refetchExplanation: vi.fn(),
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>(
    "react-router-dom",
  );
  return {
    ...actual,
    useParams: () => ({ slug: "colgan-air-3407" }),
  };
});

vi.mock("../features/cases/api", () => ({
  useCaseAudit: mocks.useCaseAudit,
  useFieldExplanation: mocks.useFieldExplanation,
}));

const auditFixture: PageAuditResponse = {
  slug: "colgan-air-3407",
  canonicalEventId: "event-1",
  summary: "1 field is disputed and requires review.",
  confidence: "medium",
  confidenceMeaning: "Mostly sourced but contains one disputed field.",
  projectionVersion: 3,
  lastUpdatedAt: "2026-06-08T12:00:00Z",
  fields: [
    {
      fieldName: "operator",
      currentValue: "Colgan Air",
      isDisputed: false,
      isManuallyOverridden: false,
      confidence: "high",
      plainEnglish: "Operator is backed by the selected source.",
    },
    {
      fieldName: "ntsb_accession_number",
      currentValue: "__DISPUTED__",
      isDisputed: true,
      isManuallyOverridden: false,
      confidence: "low",
      plainEnglish: "Multiple sources disagree on this accession number.",
    },
    {
      fieldName: "report_number",
      currentValue: "AAR-10/01",
      isDisputed: false,
      isManuallyOverridden: true,
      confidence: "medium",
      plainEnglish: "A reviewer manually selected the report number.",
    },
  ],
};

const explanationFixture: FieldExplanationResponse = {
  eventId: "event-1",
  fieldName: "operator",
  hasWinner: true,
  winner: {
    fieldName: "operator",
    currentValue: "Colgan Air",
    plainEnglish: "NTSB final report supports this operator.",
    sourceName: "NTSB eADMS",
    sourceKind: "GOVERNMENT",
  },
  losers: [
    {
      sourceName: "Newswire archive",
      sourceKind: "MEDIA",
      reportedValue: "Continental Connection",
      plainEnglish: "This source describes the marketed service, not the operator.",
    },
  ],
  losersTruncated: false,
  conflict: null,
};

describe("Audit", () => {
  beforeEach(() => {
    mocks.useCaseAudit.mockReturnValue({
      data: auditFixture,
      isLoading: false,
      error: null,
      refetch: mocks.refetchAudit,
    });
    mocks.useFieldExplanation.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
      refetch: mocks.refetchExplanation,
    });
  });

  it("renders readable field labels and audit review metrics", () => {
    render(<Audit />);

    expect(screen.getByText("Evidence Audit")).toBeInTheDocument();
    expect(screen.getByText("NTSB Accession Number")).toBeInTheDocument();
    expect(screen.getByText("Report Number")).toBeInTheDocument();
    expect(screen.getByText("1 field is disputed and requires review.")).toBeInTheDocument();
    expect(screen.getAllByText("Disputed").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Overrides").length).toBeGreaterThan(0);
  });

  it("filters fields by search text and status", () => {
    render(<Audit />);

    fireEvent.change(screen.getByPlaceholderText(/Search fields/i), {
      target: { value: "operator" },
    });

    expect(screen.getByText("Operator")).toBeInTheDocument();
    expect(screen.queryByText("NTSB Accession Number")).not.toBeInTheDocument();

    fireEvent.change(screen.getByDisplayValue("All fields"), {
      target: { value: "disputed" },
    });

    expect(screen.getByText("No audit fields match the current filters")).toBeInTheDocument();
  });

  it("opens a clicked field explanation panel", () => {
    mocks.useFieldExplanation.mockReturnValue({
      data: explanationFixture,
      isLoading: false,
      isError: false,
      refetch: mocks.refetchExplanation,
    });

    render(<Audit />);
    fireEvent.click(screen.getByText("Operator"));

    expect(mocks.useFieldExplanation).toHaveBeenLastCalledWith(
      "colgan-air-3407",
      "operator",
    );
    expect(screen.getByText("Winning source")).toBeInTheDocument();
    expect(screen.getByText("NTSB eADMS")).toBeInTheDocument();
    expect(screen.getByText("Other evidence")).toBeInTheDocument();
  });
});
