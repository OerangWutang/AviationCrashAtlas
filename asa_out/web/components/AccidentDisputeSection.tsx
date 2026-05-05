/**
 * DisputedSection — renders all open field-level claim conflicts.
 *
 * Safety-critical invariants (v20 honesty pass — do not weaken):
 *
 * 1. ONLY show "Displayed value: X" when there is genuinely a winning claim
 *    for the field.  Open disputes with no winner show
 *    "No projected value while this dispute is open." instead.
 *
 * 2. NEVER invent the projection rationale.  If the backend supplies a
 *    ProjectionExplanation with selection_reason, render that.  If not,
 *    say nothing — do not synthesise a fake justification.
 *
 * 3. Render both claim sides symmetrically.  Do not elevate one side.
 */
import type { AccidentProvenance, Claim, ProjectionExplanation } from '../types';
import { SectionTitle } from './SectionHelpers';

interface Props {
  provenance: AccidentProvenance;
}

/**
 * Map backend selection_reason codes to short human strings.
 * Unknown codes pass through unchanged (underscores → spaces) so a
 * backend that adds new reasons doesn't get its strings dropped.
 */
function humanizeSelectionReason(code: string): string {
  switch (code) {
    case 'only_active_claim':            return 'only active claim';
    case 'selected_official_final':      return 'selected from official final report';
    case 'selected_latest_official':     return 'selected from latest official source';
    case 'selected_higher_tier':         return 'selected from higher-tier source';
    case 'withheld_open_dispute':        return 'withheld because open dispute exists';
    case 'withheld_no_active_claim':     return 'no active claim to project';
    case 'approximate_nearest_city_only': return 'approximate — only nearest city is available';
    default:                             return code.replace(/_/g, ' ');
  }
}

function DisputeRow({ claim, label }: { claim: Claim; label: string }) {
  return (
    <div
      className={`flex items-center gap-2 px-2.5 py-1.5 rounded border text-[11px] ${
        claim.is_winning
          ? 'border-stone-300 bg-white'
          : 'border-stone-100 bg-stone-50 opacity-70'
      }`}
    >
      <span
        className="text-[9px] px-1.5 py-0.5 rounded bg-blue-50 text-blue-700 flex-shrink-0"
        style={{ fontFamily: 'var(--ff-mono)' }}
      >
        {label}
      </span>
      <span className="font-medium text-stone-700 flex-1">{claim.display_value}</span>
      {claim.is_winning && (
        <span
          className="text-[9px] px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-700 flex-shrink-0"
          style={{ fontFamily: 'var(--ff-mono)' }}
        >
          displayed
        </span>
      )}
    </div>
  );
}

export default function DisputedSection({ provenance }: Props) {
  const open = provenance.conflicts.filter((c) => c.status === 'open');
  if (open.length === 0) return null;

  const projections: ProjectionExplanation[] = provenance.projections ?? [];
  const explanationFor = (field: string): ProjectionExplanation | undefined =>
    projections.find((p) => p.field_name === field);

  return (
    <div className="mb-6">
      <SectionTitle>What is disputed?</SectionTitle>
      <div className="space-y-3">
        {open.map((conflict) => {
          const claimA = provenance.claims.find((c) => c.id === conflict.claim_a_id);
          const claimB = provenance.claims.find((c) => c.id === conflict.claim_b_id);
          // Real winning claim only — do NOT fall back to claimA when no winner
          // exists.  A "Displayed value" line over an open dispute with no winner
          // creates false authority.
          const winning: Claim | null = [claimA, claimB].find((c) => c?.is_winning) ?? null;
          const explanation = explanationFor(conflict.field_name);
          const field = conflict.field_name.replace(/_/g, ' ');

          return (
            <div key={conflict.id} className="rounded-lg border border-red-100 bg-red-50/50 p-3">
              <div className="flex items-center justify-between mb-2">
                <div
                  className="text-[10px] font-medium text-red-600 uppercase tracking-wide"
                  style={{ fontFamily: 'var(--ff-mono)' }}
                >
                  {field}
                </div>
                <span
                  className="text-[9px] px-1.5 py-0.5 rounded bg-red-100 text-red-700 font-medium"
                  style={{ fontFamily: 'var(--ff-mono)' }}
                >
                  Status: Open dispute
                </span>
              </div>
              <div className="grid grid-cols-1 gap-1.5">
                {claimA && (
                  <DisputeRow claim={claimA} label={claimA.source_short_name ?? 'Source A'} />
                )}
                {claimB && (
                  <DisputeRow claim={claimB} label={claimB.source_short_name ?? 'Source B'} />
                )}
              </div>
              {winning ? (
                <div
                  className="mt-2 text-[10px] text-stone-500"
                  style={{ fontFamily: 'var(--ff-mono)' }}
                >
                  Displayed value:{' '}
                  <span className="text-stone-700 font-medium">{winning.display_value}</span>
                  {/* Render explanation only if backend provided one — never fabricate. */}
                  {explanation?.selection_reason && (
                    <> · {humanizeSelectionReason(explanation.selection_reason)}</>
                  )}
                </div>
              ) : (
                <div
                  className="mt-2 text-[10px] text-stone-500"
                  style={{ fontFamily: 'var(--ff-mono)' }}
                >
                  No projected value while this dispute is open.
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
