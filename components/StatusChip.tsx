import type { ConflictStatus, ClaimType } from "../types/api";

interface StatusChipProps {
  status: ConflictStatus | ClaimType | string;
  className?: string;
}

const statusConfig: Record<
  string,
  { label: string; classes: string }
> = {
  OPEN: {
    label: "Disputed",
    classes: "chip chip-disputed",
  },
  RESOLVED: {
    label: "Resolved",
    classes: "chip chip-resolved",
  },
  RAW: {
    label: "Raw claim",
    classes: "chip chip-raw",
  },
  CONFIRMED: {
    label: "Confirmed",
    classes: "chip chip-resolved",
  },
  MANUAL_OVERRIDE: {
    label: "Manual override",
    classes: "chip bg-atlas-100 text-atlas-800 border border-atlas-200",
  },
  SUPERSEDED: {
    label: "Superseded",
    classes: "chip chip-superseded",
  },
};

export function StatusChip({ status, className = "" }: StatusChipProps) {
  const config = statusConfig[status] ?? {
    label: status,
    classes: "chip bg-gray-100 text-gray-600 border border-gray-200",
  };

  return (
    <span className={`${config.classes} ${className}`}>{config.label}</span>
  );
}

/** Confidence band chip — evidence support, NOT causal probability. */
export function ConfidenceChip({
  confidence,
}: {
  confidence: string;
}) {
  const classes: Record<string, string> = {
    high: "chip bg-resolved-50 text-resolved-700 border border-resolved-200",
    medium: "chip bg-disputed-50 text-disputed-700 border border-disputed-200",
    low: "chip bg-destructive-50 text-destructive-700 border border-destructive-200",
    unknown: "chip bg-gray-100 text-gray-500 border border-gray-200",
  };

  const labels: Record<string, string> = {
    high: "Well sourced",
    medium: "Mostly sourced",
    low: "Partially sourced",
    unknown: "Weakly sourced",
  };

  return (
    <span className={classes[confidence] ?? classes.unknown}>
      {labels[confidence] ?? confidence}
    </span>
  );
}

/** Reliability tier chip for sources. */
export function ReliabilityChip({ tier }: { tier: number }) {
  const labels = ["—", "Tier 1", "Tier 2", "Tier 3", "Tier 4", "Tier 5"];
  const colors: Record<number, string> = {
    1: "chip bg-resolved-50 text-resolved-700 border border-resolved-200",
    2: "chip bg-atlas-50 text-atlas-700 border border-atlas-200",
    3: "chip bg-disputed-50 text-disputed-700 border border-disputed-200",
    4: "chip bg-destructive-50 text-destructive-600 border border-destructive-200",
    5: "chip bg-gray-100 text-gray-500 border border-gray-200",
  };

  return (
    <span className={colors[tier] ?? colors[5]}>
      {labels[tier] ?? `Tier ${tier}`}
    </span>
  );
}
