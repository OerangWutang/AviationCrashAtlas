import { useEffect, useState } from 'react';

interface Props {
  apiKey: string;
  onApiKeyChange: (apiKey: string) => void;
  compact?: boolean;
}

export default function ReviewerAuthControl({ apiKey, onApiKeyChange, compact = false }: Props) {
  const [draft, setDraft] = useState(apiKey);
  const [expanded, setExpanded] = useState(!apiKey);

  useEffect(() => {
    setDraft(apiKey);
    if (apiKey) setExpanded(false);
  }, [apiKey]);

  const hasKey = apiKey.length > 0;
  const changed = draft.trim() !== apiKey;

  function save() {
    onApiKeyChange(draft);
    setExpanded(false);
  }

  function clear() {
    setDraft('');
    onApiKeyChange('');
    setExpanded(true);
  }

  if (!expanded && hasKey) {
    return (
      <div className="flex items-center gap-2 text-[10px]" style={{ fontFamily: 'var(--ff-mono)' }}>
        <span className="px-2 py-1 rounded border bg-emerald-50 text-emerald-700 border-emerald-200">
          reviewer key active
        </span>
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="text-stone-400 hover:text-stone-700 transition-colors"
        >
          change
        </button>
        <button
          type="button"
          onClick={clear}
          className="text-stone-400 hover:text-red-600 transition-colors"
        >
          clear
        </button>
      </div>
    );
  }

  return (
    <div
      className={[
        'rounded-lg border border-stone-200 bg-white',
        compact ? 'p-2' : 'p-3',
      ].join(' ')}
    >
      <div className="flex items-center gap-2">
        <label className="text-[10px] text-stone-400 uppercase tracking-wider whitespace-nowrap" style={{ fontFamily: 'var(--ff-mono)' }}>
          Reviewer API key
        </label>
        <input
          type="password"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') save(); }}
          placeholder="X-API-Key"
          autoComplete="off"
          className="w-48 text-[11px] px-2 py-1 rounded border border-stone-200 bg-stone-50 text-stone-700 placeholder-stone-300 focus:outline-none focus:border-stone-400 focus:bg-white transition-colors"
          style={{ fontFamily: 'var(--ff-mono)' }}
        />
        <button
          type="button"
          onClick={save}
          disabled={!changed && hasKey}
          className={[
            'px-2 py-1 rounded text-[10px] transition-colors',
            changed || !hasKey
              ? 'bg-stone-800 text-white hover:bg-stone-700'
              : 'bg-stone-100 text-stone-400 cursor-not-allowed',
          ].join(' ')}
          style={{ fontFamily: 'var(--ff-mono)' }}
        >
          save
        </button>
        {hasKey && (
          <button
            type="button"
            onClick={() => { setDraft(apiKey); setExpanded(false); }}
            className="text-[10px] text-stone-400 hover:text-stone-600 transition-colors"
            style={{ fontFamily: 'var(--ff-mono)' }}
          >
            cancel
          </button>
        )}
      </div>
      {!compact && (
        <p className="text-[10px] text-stone-400 mt-1.5" style={{ fontFamily: 'var(--ff-mono)' }}>
          Stored only in this browser. Sent as X-API-Key for conflict resolution actions.
        </p>
      )}
    </div>
  );
}
