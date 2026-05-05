/**
 * ConflictResolutionForm
 *
 * Full resolution form for a single open conflict: side-by-side claim
 * comparison, resolution type picker, notes, reviewer identity, and
 * submit/error handling.
 *
 * Resolution types that require a claim selection (claim_accepted,
 * claim_rejected) activate the ClaimSide buttons; others close the
 * conflict without designating a winner.
 */
import { useState } from 'react';
import type { Claim, ClaimConflict, ConflictResolveRequest, Source } from '../types';
import { claimTypeBg, claimTypeLabel, FIELD_LABELS } from '../lib/utils';
import {
  resolveConflict,
  NotFoundError,
  ConflictError,
  ValidationError,
  ApiTimeoutError,
} from '../lib/api';

interface Props {
  conflict: ClaimConflict;
  claimA: Claim | undefined;
  claimB: Claim | undefined;
  sourceA: Source | undefined;
  sourceB: Source | undefined;
  onResolved?: () => void;
  apiKey?: string;
}

type FormState =
  | { phase: 'idle' }
  | { phase: 'submitting' }
  | { phase: 'error'; message: string }
  | { phase: 'done' };

// ── Constants ────────────────────────────────────────────────────────────────

const RESOLUTION_LABELS: Record<string, string> = {
  claim_accepted:   'Accept one claim',
  claim_rejected:   'Reject one claim',
  claims_merged:    'Merge (same fact, different wording)',
  source_corrected: 'Source issued a correction',
  not_applicable:   'Not applicable (minor formatting difference)',
  manual_override:  'Manual override',
};

const RESOLUTION_DESCRIPTIONS: Record<string, string> = {
  claim_accepted:
    'Designate one claim as authoritative.  The accepted claim will be ' +
    'restored and projected once all conflicts on this field are resolved.',
  claim_rejected:
    'Mark one claim as incorrect.  The rejected claim is permanently excluded ' +
    'from projection.  The surviving claim is automatically designated as the winner.',
  claims_merged:
    'Both claims describe the same fact in different words — close the conflict without picking a winner.',
  source_corrected:
    'The source has issued a correction that supersedes the conflicting claim.  ' +
    'Close the conflict; the corrected claim should arrive in the next ingestion run.',
  not_applicable:
    'The disagreement is a minor formatting difference, not a factual conflict.  Close without action.',
  manual_override:
    'Override the normal resolution rules.  Describe your reasoning in the notes field.',
};

function fieldLabel(name: string): string {
  return FIELD_LABELS[name as keyof typeof FIELD_LABELS] ?? name.replace(/_/g, ' ');
}

// ── ClaimSide button ────────────────────────────────────────────────────────

interface ClaimSideProps {
  claim: Claim;
  source: Source | undefined;
  label: string;
  selected: boolean;
  rejected: boolean;
  onClick: () => void;
  disabled: boolean;
}

function ClaimSide({ claim, source, label, selected, rejected, onClick, disabled }: ClaimSideProps) {
  const typeBg = claimTypeBg(claim.claim_type);
  const stateLabel = selected ? ', selected as winner' : rejected ? ', marked as rejected' : '';
  const ariaLabel = `${label}: ${claim.display_value || 'no value'}${
    source ? ` from ${source.short_name}` : ''
  }${stateLabel}. Click to ${selected || rejected ? 'change' : 'select'}.`;

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-pressed={selected || rejected}
      aria-label={ariaLabel}
      className={[
        'flex-1 text-left p-4 rounded-xl border-2 transition-all duration-150 min-w-0',
        'focus:outline-none focus:ring-2 focus:ring-stone-400 focus:ring-offset-2',
        selected
          ? 'border-emerald-400 bg-emerald-50 shadow-sm'
          : rejected
          ? 'border-red-200 bg-red-50 opacity-60'
          : 'border-stone-200 bg-white hover:border-stone-300 hover:bg-stone-50',
        disabled ? 'cursor-default' : 'cursor-pointer',
      ].join(' ')}
    >
      <div className="text-[9px] text-stone-400 uppercase tracking-widest mb-2" style={{ fontFamily: 'var(--ff-mono)' }}>
        {label}
      </div>
      <div className="text-[15px] font-semibold text-stone-800 leading-snug break-words mb-3">
        {claim.display_value || '—'}
      </div>
      <div className="flex items-center gap-1.5 flex-wrap">
        {source && (
          <span className="text-[9px] px-2 py-0.5 rounded bg-blue-50 text-blue-600" style={{ fontFamily: 'var(--ff-mono)' }}>
            {source.short_name}
          </span>
        )}
        {source && (
          <span className="text-[9px] px-2 py-0.5 rounded bg-stone-100 text-stone-500" style={{ fontFamily: 'var(--ff-mono)' }}>
            tier {source.tier}
          </span>
        )}
        <span className={`text-[9px] px-2 py-0.5 rounded ${typeBg}`} style={{ fontFamily: 'var(--ff-mono)' }}>
          {claimTypeLabel(claim.claim_type)}
        </span>
      </div>
      {selected && (
        <div className="mt-3 text-[10px] font-medium text-emerald-700 flex items-center gap-1">
          <span>✓</span><span>Selected as winner</span>
        </div>
      )}
      {rejected && (
        <div className="mt-3 text-[10px] font-medium text-red-500 flex items-center gap-1">
          <span>✕</span><span>Marked as rejected</span>
        </div>
      )}
    </button>
  );
}

// ── Main form ────────────────────────────────────────────────────────────────

export default function ConflictResolutionForm({
  conflict, claimA, claimB, sourceA, sourceB, onResolved, apiKey,
}: Props) {
  const [resType, setResType] = useState<ConflictResolveRequest['resolution_type'] | ''>('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [rejectedId, setRejectedId] = useState<string | null>(null);
  const [notes, setNotes] = useState('');
  const [resolvedBy, setResolvedBy] = useState('');
  const [formState, setFormState] = useState<FormState>({ phase: 'idle' });

  const claimAId = conflict.claim_a_id;
  const claimBId = conflict.claim_b_id;

  function handleClaimClick(id: string) {
    if (!resType) return;
    if (resType === 'claim_accepted') {
      setSelectedId(id); setRejectedId(null);
    } else if (resType === 'claim_rejected') {
      setRejectedId(id); setSelectedId(null);
    }
  }

  function isValid(): boolean {
    if (!resType) return false;
    if (resType === 'claim_accepted' && !selectedId) return false;
    if (resType === 'claim_rejected' && !rejectedId) return false;
    return true;
  }

  async function handleSubmit() {
    if (!isValid()) return;
    setFormState({ phase: 'submitting' });

    const body: ConflictResolveRequest = {
      resolution_type: resType as ConflictResolveRequest['resolution_type'],
      resolution: notes || null,
      resolved_by: resolvedBy || null,
    };
    if (resType === 'claim_accepted') body.accepted_claim_id = selectedId;
    else if (resType === 'claim_rejected') body.rejected_claim_ids = rejectedId ? [rejectedId] : [];

    try {
      await resolveConflict(conflict.id, body, apiKey);
      setFormState({ phase: 'done' });
      onResolved?.();
    } catch (err) {
      // Map typed API errors to actionable messages instead of raw status codes.
      let message: string;
      if (err instanceof NotFoundError) {
        message =
          'This conflict no longer exists. It may have been removed or the page is stale. ' +
          'Refresh to see the current queue.';
      } else if (err instanceof ConflictError) {
        message =
          'This conflict was already resolved by another reviewer. ' +
          'Refresh the page to see the updated state.';
      } else if (err instanceof ValidationError) {
        message =
          'Invalid resolution request. Make sure you have selected a claim where required, ' +
          'and that all required fields are filled in.';
      } else if (err instanceof ApiTimeoutError) {
        message =
          'The request timed out. The server may be busy — wait a moment and try again.';
      } else {
        message = err instanceof Error ? err.message : 'Unknown error';
      }
      setFormState({ phase: 'error', message });
    }
  }

  if (formState.phase === 'done') {
    return (
      <div className="p-4 rounded-xl bg-emerald-50 border border-emerald-200 text-[12px] text-emerald-700 flex items-center gap-2">
        <span>✓</span>
        <span>Conflict resolved. Provenance reloading…</span>
      </div>
    );
  }

  const needsClaimPick = resType === 'claim_accepted' || resType === 'claim_rejected';
  const hasReviewerApiKey = Boolean(apiKey);

  return (
    <div className="border-2 border-red-200 rounded-xl bg-white overflow-hidden">
      {/* Header */}
      <div className="flex items-start justify-between gap-3 px-4 py-3 bg-red-50 border-b border-red-100">
        <div>
          <div className="text-[10px] text-red-400 uppercase tracking-widest mb-0.5" style={{ fontFamily: 'var(--ff-mono)' }}>
            open conflict
          </div>
          <div className="text-[14px] font-semibold text-stone-800">
            {fieldLabel(conflict.field_name)}
          </div>
        </div>
        <span className="flex-shrink-0 text-[10px] px-2 py-1 rounded bg-red-100 text-red-600 mt-0.5" style={{ fontFamily: 'var(--ff-mono)' }}>
          unresolved
        </span>
      </div>

      <div className="p-4 space-y-4">
        {/* Claim comparison */}
        {claimA && claimB && (
          <div className="flex gap-3">
            <ClaimSide
              claim={claimA} source={sourceA} label="Claim A"
              selected={resType === 'claim_accepted' && selectedId === claimAId}
              rejected={resType === 'claim_rejected' && rejectedId === claimAId}
              onClick={() => handleClaimClick(claimAId)}
              disabled={!needsClaimPick || formState.phase === 'submitting'}
            />
            <div className="flex-shrink-0 flex items-center text-stone-300 text-[18px] font-light mt-4">vs</div>
            <ClaimSide
              claim={claimB} source={sourceB} label="Claim B"
              selected={resType === 'claim_accepted' && selectedId === claimBId}
              rejected={resType === 'claim_rejected' && rejectedId === claimBId}
              onClick={() => handleClaimClick(claimBId)}
              disabled={!needsClaimPick || formState.phase === 'submitting'}
            />
          </div>
        )}

        {/* Resolution type picker */}
        <div>
          <label id="resolution-type-label" className="block text-[10px] text-stone-400 uppercase tracking-widest mb-2" style={{ fontFamily: 'var(--ff-mono)' }}>
            Resolution type
          </label>
          <div className="grid grid-cols-2 gap-2" role="group" aria-labelledby="resolution-type-label">
            {(Object.entries(RESOLUTION_LABELS) as [ConflictResolveRequest['resolution_type'], string][]).map(([value, label]) => (
              <button
                key={value} type="button"
                onClick={() => { setResType(value); setSelectedId(null); setRejectedId(null); }}
                disabled={formState.phase === 'submitting'}
                aria-pressed={resType === value}
                className={[
                  'text-left px-3 py-2 rounded-lg border text-[11px] transition-all',
                  'focus:outline-none focus:ring-2 focus:ring-stone-400 focus:ring-offset-1',
                  resType === value ? 'border-stone-700 bg-stone-800 text-white' : 'border-stone-200 bg-white text-stone-600 hover:border-stone-300',
                ].join(' ')}
              >
                {label}
              </button>
            ))}
          </div>
          {resType && (
            <p className="mt-2 text-[11px] text-stone-400 leading-relaxed">
              {RESOLUTION_DESCRIPTIONS[resType]}
            </p>
          )}
        </div>

        {/* Instruction */}
        {needsClaimPick && !selectedId && !rejectedId && (
          <div className="text-[11px] text-stone-400 italic">
            {resType === 'claim_accepted'
              ? '← Click a claim above to select the authoritative value'
              : '← Click the claim above that should be rejected'}
          </div>
        )}

        {/* Notes */}
        <div>
          <label htmlFor={`conflict-notes-${conflict.id}`} className="block text-[10px] text-stone-400 uppercase tracking-widest mb-1.5" style={{ fontFamily: 'var(--ff-mono)' }}>
            Notes (optional)
          </label>
          <textarea
            id={`conflict-notes-${conflict.id}`}
            value={notes} onChange={(e) => setNotes(e.target.value)}
            disabled={formState.phase === 'submitting'} rows={2}
            placeholder="Rationale, source URL, NTSB case reference…"
            className="w-full text-[12px] px-3 py-2 rounded-lg border border-stone-200 bg-stone-50 text-stone-700 resize-none placeholder-stone-300 focus:outline-none focus:border-stone-400 focus:bg-white transition-colors"
          />
        </div>

        {/* Reviewer authentication + local-dev identity */}
        <div className="space-y-2">
          <div
            className={[
              'text-[10px] px-3 py-2 rounded-lg border',
              hasReviewerApiKey
                ? 'bg-emerald-50 border-emerald-200 text-emerald-700'
                : 'bg-amber-50 border-amber-200 text-amber-700',
            ].join(' ')}
            style={{ fontFamily: 'var(--ff-mono)' }}
          >
            {hasReviewerApiKey
              ? 'Reviewer API key will be sent as X-API-Key. The backend will use the authenticated operator in the audit trail.'
              : 'No reviewer API key saved. This only works when API_AUTH_ENABLED=false; production backends will return 401.'}
          </div>

          <div>
            <label htmlFor={`conflict-reviewer-${conflict.id}`} className="block text-[10px] text-stone-400 uppercase tracking-widest mb-1.5" style={{ fontFamily: 'var(--ff-mono)' }}>
              Your name / email
            </label>
            <input
              id={`conflict-reviewer-${conflict.id}`} type="text"
              value={resolvedBy} onChange={(e) => setResolvedBy(e.target.value)}
              disabled={formState.phase === 'submitting'}
              placeholder="reviewer@example.com" autoComplete="email"
              className="w-full text-[12px] px-3 py-2 rounded-lg border border-stone-200 bg-stone-50 text-stone-700 placeholder-stone-300 focus:outline-none focus:border-stone-400 focus:bg-white transition-colors"
            />
            <p className="text-[10px] text-stone-300 mt-1" style={{ fontFamily: 'var(--ff-mono)' }}>
              Used only when API_AUTH_ENABLED=false. Auth-enabled backends derive the operator from the API key.
            </p>
          </div>
        </div>

        {/* Error */}
        {formState.phase === 'error' && (
          <div role="alert" aria-live="polite" className="p-3 rounded-lg bg-red-50 border border-red-200 text-[11px] text-red-700">
            {formState.message}
          </div>
        )}

        {/* Submit */}
        <div className="flex items-center gap-3 pt-1">
          <button
            type="button" onClick={handleSubmit}
            disabled={!isValid() || formState.phase === 'submitting'}
            className={[
              'px-5 py-2 rounded-lg text-[12px] font-medium transition-all',
              isValid() && formState.phase !== 'submitting' ? 'bg-stone-800 text-white hover:bg-stone-700' : 'bg-stone-100 text-stone-400 cursor-not-allowed',
            ].join(' ')}
          >
            {formState.phase === 'submitting' ? 'Submitting…' : 'Submit resolution'}
          </button>
          {formState.phase === 'error' && (
            <button
              type="button" onClick={() => setFormState({ phase: 'idle' })}
              className="text-[11px] text-stone-400 hover:text-stone-600 transition-colors"
            >
              Dismiss error
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
