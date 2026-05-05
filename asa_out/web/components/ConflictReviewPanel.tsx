/**
 * ConflictReviewPanel
 *
 * Renders each open claim conflict as a side-by-side comparison of the two
 * conflicting claims with a resolution form.  Completed (resolved/obsolete)
 * conflicts are shown collapsed in an audit trail below.
 *
 * Props
 * -----
 * provenance   — full AccidentProvenance (claims, conflicts, sources)
 * onResolved   — callback fired after a successful resolution so the parent
 *                can re-fetch provenance and update the accident record
 * apiKey       — optional API key forwarded to resolveConflict()
 */

import { useState } from 'react';
import type {
  AccidentProvenance,
  Claim,
  ClaimConflict,
  Source,
} from '../types';
import { FIELD_LABELS, formatDate } from '../lib/utils';
import ConflictResolutionForm from './ConflictResolutionForm';
import { SectionTitle } from './SectionHelpers';

interface Props {
  provenance: AccidentProvenance;
  onResolved?: () => void;
  apiKey?: string;
}

// ── Small helpers ──────────────────────────────────────────────────────────

function fieldLabel(name: string): string {
  return FIELD_LABELS[name] ?? name.replace(/_/g, ' ');
}


// ── Resolved conflict summary ──────────────────────────────────────────────

function ResolvedConflict({
  conflict,
  claimA,
  claimB,
  sourceA,
  sourceB,
}: {
  conflict: ClaimConflict;
  claimA: Claim | undefined;
  claimB: Claim | undefined;
  sourceA: Source | undefined;
  sourceB: Source | undefined;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="border border-stone-100 rounded-lg bg-stone-50 overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-3 px-4 py-2.5 text-left hover:bg-stone-100 transition-colors"
      >
        <span className="text-[10px] text-stone-400 flex-shrink-0" style={{ fontFamily: 'var(--ff-mono)' }}>
          {conflict.status === 'resolved' ? '✓ resolved' : '○ obsolete'}
        </span>
        <span className="text-[11px] text-stone-600 font-medium flex-1">
          {fieldLabel(conflict.field_name)}
        </span>
        <span className="text-[10px] text-stone-400" style={{ fontFamily: 'var(--ff-mono)' }}>
          {conflict.resolution_type?.replace(/_/g, ' ')}
        </span>
        <span className="text-[10px] text-stone-300">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="px-4 pb-4 pt-1 border-t border-stone-100">
          {/* Claim comparison */}
          <div className="grid grid-cols-2 gap-3 mb-3 mt-2">
            {[
              { claim: claimA, source: sourceA, id: conflict.claim_a_id },
              { claim: claimB, source: sourceB, id: conflict.claim_b_id },
            ].map(({ claim, source, id }) => (
              <div
                key={id}
                className={[
                  'p-3 rounded-lg border text-[11px]',
                  conflict.accepted_claim_id === id
                    ? 'border-emerald-200 bg-emerald-50'
                    : conflict.rejected_claim_ids?.includes(id)
                    ? 'border-red-100 bg-red-50 opacity-60'
                    : 'border-stone-200 bg-white',
                ].join(' ')}
              >
                <div className="text-stone-500 mb-1" style={{ fontFamily: 'var(--ff-mono)' }}>
                  {source?.short_name ?? id.slice(0, 8)}
                </div>
                <div className="font-medium text-stone-800">
                  {claim?.display_value ?? '—'}
                </div>
                {conflict.accepted_claim_id === id && (
                  <div className="text-emerald-600 mt-1">✓ accepted</div>
                )}
                {conflict.rejected_claim_ids?.includes(id) && (
                  <div className="text-red-500 mt-1">✕ rejected</div>
                )}
              </div>
            ))}
          </div>

          {/* Resolution metadata */}
          <div className="text-[10px] text-stone-400 space-y-0.5" style={{ fontFamily: 'var(--ff-mono)' }}>
            {conflict.resolved_by && (
              <div>resolved by: <span className="text-stone-600">{conflict.resolved_by}</span></div>
            )}
            {conflict.resolved_at && (
              <div>resolved: <span className="text-stone-600">{formatDate(conflict.resolved_at)}</span></div>
            )}
            {conflict.resolution && (
              <div className="mt-1 text-stone-500 text-[11px] not-italic">{conflict.resolution}</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────

export default function ConflictReviewPanel({ provenance, onResolved, apiKey }: Props) {
  const claimsById = Object.fromEntries(provenance.claims.map((c) => [c.id, c]));
  const sourcesById = Object.fromEntries(provenance.sources.map((s) => [s.id, s]));

  const open = provenance.conflicts.filter((c) => c.status === 'open');
  const closed = provenance.conflicts.filter((c) => c.status !== 'open');

  if (provenance.conflicts.length === 0) {
    return (
      <div className="py-6 text-center text-[12px] text-stone-400 italic">
        No conflicts for this event.
      </div>
    );
  }

  function claimsForConflict(cf: ClaimConflict) {
    const a = claimsById[cf.claim_a_id];
    const b = claimsById[cf.claim_b_id];
    const sa = a ? sourcesById[a.source_id] : undefined;
    const sb = b ? sourcesById[b.source_id] : undefined;
    return { a, b, sa, sb };
  }

  return (
    <div className="space-y-4">
      {/* Open conflicts */}
      {open.length > 0 && (
        <div className="space-y-4">
          <SectionTitle>
            ⚠ {open.length} open conflict{open.length > 1 ? 's' : ''}
          </SectionTitle>
          {open.map((cf) => {
            const { a, b, sa, sb } = claimsForConflict(cf);
            return (
              <ConflictResolutionForm
                key={cf.id}
                conflict={cf}
                claimA={a}
                claimB={b}
                sourceA={sa}
                sourceB={sb}
                onResolved={onResolved}
                apiKey={apiKey}
              />
            );
          })}
        </div>
      )}

      {/* Resolved / obsolete conflicts */}
      {closed.length > 0 && (
        <div className="space-y-2 mt-2">
          <SectionTitle>Resolved conflicts</SectionTitle>
          {closed.map((cf) => {
            const { a, b, sa, sb } = claimsForConflict(cf);
            return (
              <ResolvedConflict
                key={cf.id}
                conflict={cf}
                claimA={a}
                claimB={b}
                sourceA={sa}
                sourceB={sb}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}

