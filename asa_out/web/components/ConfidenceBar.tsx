import { confColor } from '../lib/utils';

interface Factor {
  name: string;
  delta: number;
  reason: string;
}

interface Props {
  score: number;
  label: string;
  factors?: Factor[];
}

export default function ConfidenceBar({ score, label, factors }: Props) {
  const color = confColor(score);
  const pct = (score * 100).toFixed(1);

  return (
    <div className="bg-stone-50 border border-stone-200 rounded-lg p-4 mb-4">
      <div className="flex items-center justify-between mb-2">
        <span
          className="text-[11px] text-stone-400"
          style={{ fontFamily: 'var(--ff-mono)' }}
        >
          source completeness
        </span>
        <span
          className="text-[18px] font-medium tabular-nums"
          style={{ fontFamily: 'var(--ff-mono)', color }}
        >
          {score.toFixed(3)}
        </span>
      </div>

      {/* Bar track */}
      <div className="h-1.5 bg-stone-200 rounded-full overflow-hidden mb-3">
        <div
          className="h-full rounded-full conf-bar-fill"
          style={{ width: `${pct}%`, background: color }}
        />
      </div>

      {/* Factor pills */}
      {factors && factors.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {factors.map((f, i) => (
            <span
              key={i}
              title={f.reason}
              className={`text-[10px] px-1.5 py-0.5 rounded border cursor-help ${
                f.delta > 0
                  ? 'bg-emerald-50 text-emerald-600 border-emerald-200'
                  : f.delta < 0
                  ? 'bg-red-50 text-red-500 border-red-200'
                  : 'bg-stone-50 text-stone-400 border-stone-200'
              }`}
              style={{ fontFamily: 'var(--ff-mono)' }}
            >
              {f.delta > 0 ? '+' : ''}{f.delta !== 0 ? f.delta.toFixed(2) + ' ' : ''}{f.name.replace(/_/g, ' ')}
            </span>
          ))}
        </div>
      )}

      {/* No factors available — say so honestly rather than inventing them */}
      {(!factors || factors.length === 0) && (
        <div
          className="text-[10px] text-stone-400 italic"
          style={{ fontFamily: 'var(--ff-mono)' }}
        >
          breakdown unavailable
        </div>
      )}
    </div>
  );
}
