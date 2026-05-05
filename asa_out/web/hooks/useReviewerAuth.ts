import { useCallback, useEffect, useState } from 'react';

/**
 * Browser-local reviewer API-key state.
 *
 * The backend accepts reviewer/admin keys in the X-API-Key header.  The key is
 * intentionally never placed in URLs, query params, or server-rendered props;
 * it stays in localStorage and is sent only by reviewer write actions.
 */
export const REVIEWER_API_KEY_STORAGE = 'asa.reviewer_api_key';
export const REVIEWER_AUTH_CHANGED_EVENT = 'asa:reviewer-auth-changed';

function readStoredKey(): string {
  if (typeof window === 'undefined') return '';
  return window.localStorage.getItem(REVIEWER_API_KEY_STORAGE) ?? '';
}

function emitAuthChanged() {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent(REVIEWER_AUTH_CHANGED_EVENT));
}

export function useReviewerAuth() {
  const [apiKey, setApiKeyState] = useState('');
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    setApiKeyState(readStoredKey());
    setHydrated(true);

    function handleAuthChanged() {
      setApiKeyState(readStoredKey());
    }

    window.addEventListener(REVIEWER_AUTH_CHANGED_EVENT, handleAuthChanged);
    window.addEventListener('storage', handleAuthChanged);
    return () => {
      window.removeEventListener(REVIEWER_AUTH_CHANGED_EVENT, handleAuthChanged);
      window.removeEventListener('storage', handleAuthChanged);
    };
  }, []);

  const setApiKey = useCallback((value: string) => {
    const trimmed = value.trim();
    if (typeof window !== 'undefined') {
      if (trimmed) {
        window.localStorage.setItem(REVIEWER_API_KEY_STORAGE, trimmed);
      } else {
        window.localStorage.removeItem(REVIEWER_API_KEY_STORAGE);
      }
    }
    setApiKeyState(trimmed);
    emitAuthChanged();
  }, []);

  const clearApiKey = useCallback(() => setApiKey(''), [setApiKey]);

  return {
    apiKey,
    hasApiKey: apiKey.length > 0,
    hydrated,
    setApiKey,
    clearApiKey,
  };
}
