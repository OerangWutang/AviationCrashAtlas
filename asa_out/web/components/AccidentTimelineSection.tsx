/**
 * AccidentTimelineSection
 *
 * Shows how a record evolved over time.  Prefers backend-supplied
 * event_revisions (written by the ingestion pipeline) over the derived
 * timeline we construct from documents and conflict resolutions.  The
 * fallback DerivedTimeline is used for records ingested before revision
 * tracking was added in migration 0008.
 */
import type { AccidentDetail, AccidentProvenance, EventRevision } from '../types';
import { formatDate } from '../lib/utils';
import { SectionTitle } from './SectionHelpers';

interface Props {
  provenance: AccidentProvenance;
  accident: AccidentDetail;
}

function humanizeRevision(r: EventRevision): string {
  const fields =
    r.field_names && r.field_names.length > 0
      ? r.field_names.map((f) => f.replace(/_/g, ' ')).join(', ')
      : null;
  const src = r.source_short_name ?? 'A source';
  switch (r.revision_type) {
    case 'source_record_first_seen':    return `${src} first published this record.`;
    case 'source_snapshot_changed':     return `${src} updated this record${fields ? ` (${fields})` : ''}.`;
    case 'source_record_unchanged':     return `${src} re-checked this record (no content change).`;
    case 'source_field_added':          return `${src} added field${fields ? `s: ${fields}` : ''}.`;
    case 'source_field_removed':        return `${src} removed field${fields ? `s: ${fields}` : ''}.`;
    case 'source_field_value_changed':  return `${src} changed value${fields ? ` for ${fields}` : ''}.`;
    case 'claim_superseded':            return `Claim superseded${fields ? ` for ${fields}` : ''}.`;
    case 'conflict_opened':             return `Conflict opened${fields ? ` on ${fields}` : ''}.`;
    case 'conflict_resolved':           return `Conflict resolved${fields ? ` on ${fields}` : ''}.`;
    case 'conflict_obsoleted':          return `Conflict obsoleted${fields ? ` on ${fields}` : ''}.`;
    case 'source_document_linked':      return 'Source document linked.';
    case 'source_document_verified':    return 'Source document verified as available.';
    case 'source_document_unavailable': return 'Source document marked unavailable.';
    case 'projection_rebuilt':          return 'Record rebuilt from current claims.';
    default:                            return r.revision_type.replace(/_/g, ' ');
  }
}

function TimelineDot({ isLast }: { isLast: boolean }) {
  return (
    <div className="flex flex-col items-center">
      <div className="w-1.5 h-1.5 rounded-full bg-stone-300 mt-1.5 flex-shrink-0" />
      {!isLast && <div className="w-px flex-1 bg-stone-200 mt-1" />}
    </div>
  );
}

function BackendRevisionTimeline({
  revisions,
  rebuiltAt,
}: {
  revisions: EventRevision[];
  rebuiltAt: string;
}) {
  const sorted = [...revisions].sort((a, b) => a.occurred_at.localeCompare(b.occurred_at));
  return (
    <div className="mb-6">
      <SectionTitle>How this record evolved</SectionTitle>
      <div className="space-y-0">
        {sorted.map((r, i) => (
          <div key={r.id} className="flex gap-3">
            <TimelineDot isLast={i === sorted.length - 1} />
            <div className="pb-3">
              <div
                className="text-[9px] text-stone-400"
                style={{ fontFamily: 'var(--ff-mono)' }}
              >
                {formatDate(r.occurred_at)} · {r.revision_type.replace(/_/g, ' ')}
              </div>
              <div className="text-[12px] text-stone-600">
                {r.description ?? humanizeRevision(r)}
              </div>
            </div>
          </div>
        ))}
        <div
          className="text-[10px] text-stone-300 mt-1"
          style={{ fontFamily: 'var(--ff-mono)' }}
        >
          Record last rebuilt: {formatDate(rebuiltAt)}
        </div>
      </div>
    </div>
  );
}

function DerivedTimeline({ provenance, accident }: Props) {
  type Entry = { isoDate: string; date: string; event: string };
  const entries: Entry[] = [];

  for (const doc of provenance.source_documents) {
    if (doc.published_at) {
      const src = provenance.sources.find((s) => s.id === doc.source_id);
      entries.push({
        isoDate: doc.published_at,
        date: formatDate(doc.published_at),
        event: `${src?.short_name ?? 'Source'} document published: ${doc.title ?? doc.document_type}.`,
      });
    }
  }
  for (const conflict of provenance.conflicts) {
    if (conflict.status !== 'open' && conflict.resolved_at) {
      const field = conflict.field_name.replace(/_/g, ' ');
      const type = conflict.resolution_type?.replace(/_/g, ' ') ?? 'resolved';
      entries.push({
        isoDate: conflict.resolved_at,
        date: formatDate(conflict.resolved_at),
        event: `Conflict on "${field}" ${type}.`,
      });
    }
  }
  entries.push({
    isoDate: accident.last_projected_at,
    date: formatDate(accident.last_projected_at),
    event: 'Record last rebuilt from source claims.',
  });
  entries.sort((a, b) => a.isoDate.localeCompare(b.isoDate));

  if (entries.length <= 1) return null;

  return (
    <div className="mb-6">
      <SectionTitle>How this record evolved</SectionTitle>
      <div className="space-y-0">
        {entries.map((e, i) => (
          <div key={i} className="flex gap-3">
            <TimelineDot isLast={i === entries.length - 1} />
            <div className="pb-3">
              <div
                className="text-[9px] text-stone-400"
                style={{ fontFamily: 'var(--ff-mono)' }}
              >
                {e.date}
              </div>
              <div className="text-[12px] text-stone-600">{e.event}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function AccidentTimelineSection({ provenance, accident }: Props) {
  if (provenance.revisions && provenance.revisions.length > 0) {
    return (
      <BackendRevisionTimeline
        revisions={provenance.revisions}
        rebuiltAt={accident.last_projected_at}
      />
    );
  }
  return <DerivedTimeline provenance={provenance} accident={accident} />;
}
