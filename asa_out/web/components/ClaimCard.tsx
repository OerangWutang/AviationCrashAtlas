import type { Claim } from '../types';
import { claimTypeBg, claimTypeLabel, FIELD_LABELS } from '../lib/utils';

interface Props {
  claim: Claim;
}

export default function ClaimCard({ claim }: Props) {
  const label = FIELD_LABELS[claim.field_name] ?? claim.field_name.replace(/_/g, ' ');
  // Use the backend-provided display string. The backend's display() function
  // correctly formats coordinates, dates, booleans, and nested dicts.
  // Falling back to a raw JSON dump only if display_value is somehow absent.
  const value = claim.display_value || JSON.stringify(claim.field_value?.v);
  const typeBg = claimTypeBg(claim.claim_type);

  return (
    <div
      className={`p-3 rounded-lg border ${
        claim.is_winning ? 'border-stone-200 bg-white' : 'border-stone-100 bg-stone-50 opacity-60'
      }`}
    >
      <div
        className="text-[10px] text-stone-400 mb-1 uppercase tracking-wide"
        style={{ fontFamily: 'var(--ff-mono)' }}
      >
        {label}
      </div>
      <div className="text-[13px] font-medium text-stone-800 leading-snug break-words">
        {value}
      </div>
      <div className="flex items-center gap-1.5 mt-2 flex-wrap">
        {claim.source_short_name && (
          <span
            className="text-[9px] px-1.5 py-0.5 rounded bg-blue-50 text-blue-600"
            style={{ fontFamily: 'var(--ff-mono)' }}
          >
            {claim.source_short_name}
          </span>
        )}
        <span
          className={`text-[9px] px-1.5 py-0.5 rounded ${typeBg}`}
          style={{ fontFamily: 'var(--ff-mono)' }}
        >
          {claimTypeLabel(claim.claim_type)}
        </span>
        {claim.is_winning && (
          <span
            className="text-[9px] px-1.5 py-0.5 rounded bg-stone-100 text-stone-400"
            style={{ fontFamily: 'var(--ff-mono)' }}
          >
            winning
          </span>
        )}
      </div>
    </div>
  );
}
