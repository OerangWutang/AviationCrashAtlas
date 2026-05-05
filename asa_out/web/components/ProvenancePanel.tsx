import type { AccidentProvenance } from '../types';
import ClaimCard from './ClaimCard';
import ConflictReviewPanel from './ConflictReviewPanel';
import ErrorBoundary from './ErrorBoundary';
import { SectionTitle } from './SectionHelpers';

interface Props {
  provenance: AccidentProvenance;
  onResolved?: () => void;
  apiKey?: string;
}

const TIER_LABELS: Record<number, string> = {
  1: 'Primary (public domain)',
  2: 'Historical (licensed)',
  3: 'Reports (public)',
  4: 'Supplementary (commercial)',
};

export default function ProvenancePanel({ provenance, onResolved, apiKey }: Props) {
  const winningClaims = provenance.claims.filter((c) => c.is_winning);
  const otherClaims = provenance.claims.filter(
    (c) => !c.is_winning && c.claim_type !== 'superseded'
  );
  const unresolved = provenance.conflicts.filter((c) => c.status === 'open');

  // Build a human-readable list of truncated sections for the warning banner.
  const t = provenance.truncation;
  const truncatedSections: string[] = [];
  if (t?.claims)          truncatedSections.push(`claims (showing first ${t.claims_limit})`);
  if (t?.conflicts)       truncatedSections.push(`conflicts (showing first ${t.conflicts_limit})`);
  if (t?.source_documents) truncatedSections.push(`documents (showing first ${t.source_documents_limit})`);

  return (
    <div>
      {/* Truncation warning — shown when any sub-section was capped */}
      {truncatedSections.length > 0 && (
        <div className="mb-4 p-3 rounded-lg bg-amber-50 border border-amber-200 text-[12px] text-amber-700">
          <span className="font-medium">⚠ Partial provenance</span>
          {' '}— this event has more data than can be shown at once.
          Truncated: {truncatedSections.join('; ')}.
        </div>
      )}
      {/* Conflict review — always shown when conflicts exist */}
      {provenance.conflicts.length > 0 && (
        <>
          <SectionTitle>Conflicts</SectionTitle>
          <div className="mb-6">
            <ErrorBoundary label="Conflicts">
              <ConflictReviewPanel
                provenance={provenance}
                onResolved={onResolved}
                apiKey={apiKey}
              />
            </ErrorBoundary>
          </div>
        </>
      )}

      {/* Conflicts alert — still show count banner when there are open ones */}
      {unresolved.length > 0 && (
        <div className="mb-4 p-3 rounded-lg bg-red-50 border border-red-200 text-[12px] text-red-700">
          <span className="font-medium">⚠ {unresolved.length} unresolved conflict{unresolved.length > 1 ? 's' : ''}</span>
          {' '}— two sources disagree on{' '}
          {unresolved.map((c) => c.field_name.replace(/_/g, ' ')).join(', ')}.
          This event's source completeness score is penalised until resolved.
        </div>
      )}

      {/* Winning claims */}
      <SectionTitle>Active claims</SectionTitle>
      <p className="text-[12px] text-stone-400 mb-3">
        These are the field values currently shown in the record — one winning claim per field.
      </p>
      <div className="claim-grid mb-6">
        {winningClaims.map((c) => (
          <ClaimCard key={c.id} claim={c} />
        ))}
      </div>

      {/* Other (non-winning, non-superseded) claims */}
      {otherClaims.length > 0 && (
        <>
          <SectionTitle>Other claims</SectionTitle>
          <p className="text-[12px] text-stone-400 mb-3">
            Additional claims not currently selected as the winning value.
          </p>
          <div className="claim-grid mb-6">
            {otherClaims.map((c) => (
              <ClaimCard key={c.id} claim={c} />
            ))}
          </div>
        </>
      )}

      {/* Source documents */}
      {provenance.source_documents.length > 0 ? (
        <>
          <SectionTitle>Source documents</SectionTitle>
          <div className="space-y-2 mb-6">
            {provenance.source_documents.map((doc) => {
              const src = provenance.sources.find((s) => s.id === doc.source_id);
              return (
                <div
                  key={doc.id}
                  className="flex items-center gap-3 p-3 rounded-lg border border-stone-200 bg-white"
                >
                  {/* Source badge */}
                  <div
                    className="flex-shrink-0 w-10 h-10 rounded-lg bg-blue-50 text-blue-700 flex items-center justify-center text-[9px] font-medium"
                    style={{ fontFamily: 'var(--ff-mono)' }}
                  >
                    {src?.short_name ?? '?'}
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="text-[12px] font-medium text-stone-700 mb-0.5">
                      {doc.title ?? doc.document_type}
                    </div>
                    <a
                      href={doc.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[11px] text-blue-600 source-link truncate block"
                      style={{ fontFamily: 'var(--ff-mono)' }}
                    >
                      {doc.url}
                    </a>
                  </div>

                  <div className="flex-shrink-0 flex flex-col items-end gap-1">
                    <span
                      className="text-[9px] px-1.5 py-0.5 rounded bg-stone-100 text-stone-400"
                      style={{ fontFamily: 'var(--ff-mono)' }}
                    >
                      tier {src?.tier ?? '?'}
                    </span>
                    {doc.is_available === false && (
                      <span
                        className="text-[9px] px-1.5 py-0.5 rounded bg-red-50 text-red-500"
                        style={{ fontFamily: 'var(--ff-mono)' }}
                      >
                        link broken
                      </span>
                    )}
                    {doc.url_verified && (
                      <span
                        className="text-[9px] px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-600"
                        style={{ fontFamily: 'var(--ff-mono)' }}
                      >
                        verified ✓
                      </span>
                    )}
                    {!doc.url_verified && doc.is_available !== false && (
                      <span
                        className="text-[9px] px-1.5 py-0.5 rounded bg-amber-50 text-amber-600"
                        style={{ fontFamily: 'var(--ff-mono)' }}
                      >
                        unverified
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </>
      ) : (
        <div className="mb-6">
          <SectionTitle>Source documents</SectionTitle>
          <p className="text-[12px] text-stone-400 italic">
            No source documents are linked for this record yet. Provenance below is based on
            structured source claims, not verified report documents.
          </p>
        </div>
      )}

      {/* Sources */}
      <SectionTitle>Contributing sources</SectionTitle>
      <div className="space-y-2">
        {provenance.sources.map((src) => (
          <div
            key={src.id}
            className="flex items-start gap-3 p-3 rounded-lg border border-stone-200 bg-stone-50"
          >
            <div
              className="flex-shrink-0 w-10 h-10 rounded-lg bg-blue-50 text-blue-700 flex items-center justify-center text-[9px] font-medium"
              style={{ fontFamily: 'var(--ff-mono)' }}
            >
              {src.short_name}
            </div>
            <div>
              <div className="text-[12px] font-medium text-stone-700">{src.display_name}</div>
              <div className="text-[11px] text-stone-400">{src.description}</div>
              <div className="flex items-center gap-2 mt-1">
                <span
                  className="text-[9px] px-1.5 py-0.5 rounded bg-stone-200 text-stone-500"
                  style={{ fontFamily: 'var(--ff-mono)' }}
                >
                  {TIER_LABELS[src.tier] ?? `tier ${src.tier}`}
                </span>
                <span
                  className="text-[9px] px-1.5 py-0.5 rounded bg-stone-200 text-stone-500"
                  style={{ fontFamily: 'var(--ff-mono)' }}
                >
                  {src.license_type.replace(/_/g, ' ')}
                </span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
