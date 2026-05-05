import type { Confidence } from '../types';
import { confBg } from '../lib/utils';

interface Props {
  confidence: Confidence;
  showScore?: boolean;
}

export default function ConfidenceBadge({ confidence, showScore = false }: Props) {
  return (
    <span
      className={`inline-flex items-center gap-1 text-[10px] font-medium px-1.5 py-0.5 rounded-full border ${confBg(confidence.label)}`}
      style={{ fontFamily: 'var(--ff-mono)' }}
    >
      {confidence.label}
      {showScore && (
        <span className="opacity-70">{confidence.score.toFixed(2)}</span>
      )}
    </span>
  );
}
