/**
 * AccidentSourcesSection
 *
 * Renders each contributing source (from provenance.sources) alongside
 * the fields it won, any open conflicts it is involved in, and its linked
 * source documents.
 *
 * Document state is shown as:
 *   ✓  url_verified + is_available  → verified available
 *   ✗  is_available === false        → checked, unavailable
 *   ?  url_verified === false        → linked, not yet verified
 *
 * We deliberately distinguish all three states so an unchecked link is
 * never presented as verified.  Run `atlas check-links` to update
 * url_verified / is_available.
 */
import type { AccidentProvenance } from '../types';

interface Props {
  provenance: AccidentProvenance;
}

export default function AccidentSourcesSection({ provenance }: Props) {
  return (
    <div className="mb-4 space-y-2">
      {provenance.sources.map((src) => {
        const docs = provenance.source_documents.filter((d) => d.source_id === src.id);
        const conflictFields = provenance.conflicts
          .filter(
            (c) =>
              c.status === 'open' &&
              provenance.claims.some(
                (cl) =>
                  (cl.id === c.claim_a_id || cl.id === c.claim_b_id) &&
                  cl.source_id === src.id,
              ),
          )
          .map((c) => c.field_name.replace(/_/g, ' '));
        const winningFields = provenance.claims
          .filter((c) => c.is_winning && c.source_id === src.id)
          .map((c) => c.field_name.replace(/_/g, ' '));

        return (
          <div key={src.id} className="rounded-lg border border-stone-200 bg-white p-3">
            <div className="flex items-start gap-3">
              {/* Source badge */}
              <div
                className="flex-shrink-0 w-9 h-9 rounded-md bg-blue-50 text-blue-700 flex items-center justify-center text-[9px] font-medium"
                style={{ fontFamily: 'var(--ff-mono)' }}
              >
                {src.short_name}
              </div>

              {/* Source details */}
              <div className="flex-1 min-w-0">
                <div className="text-[12px] font-medium text-stone-800">{src.display_name}</div>
                {src.description && (
                  <div className="text-[11px] text-stone-400 mb-1">{src.description}</div>
                )}
                {winningFields.length > 0 && (
                  <div
                    className="text-[10px] text-stone-500"
                    style={{ fontFamily: 'var(--ff-mono)' }}
                  >
                    Used for:{' '}
                    <span className="text-stone-700">{winningFields.join(', ')}</span>
                  </div>
                )}
                {conflictFields.length > 0 && (
                  <div
                    className="text-[10px] text-red-500 mt-0.5"
                    style={{ fontFamily: 'var(--ff-mono)' }}
                  >
                    Conflicts with:{' '}
                    <span className="font-medium">{conflictFields.join(', ')}</span>
                  </div>
                )}
                {docs.length > 0 && (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {docs.map((d) => (
                      <a
                        key={d.id}
                        href={d.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-[10px] px-1.5 py-0.5 rounded bg-stone-100 text-blue-600 hover:bg-blue-50 source-link"
                        style={{ fontFamily: 'var(--ff-mono)' }}
                      >
                        {d.title ?? d.document_type}{' '}
                        {d.url_verified && d.is_available
                          ? '✓'
                          : d.is_available === false
                          ? '✗'
                          : '?'}
                      </a>
                    ))}
                  </div>
                )}
              </div>

              {/* Tier badge */}
              <span
                className="text-[9px] px-1.5 py-0.5 rounded bg-stone-100 text-stone-400 flex-shrink-0"
                style={{ fontFamily: 'var(--ff-mono)' }}
              >
                tier {src.tier}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
