import {
  createContext,
  useContext,
  useState,
  useCallback,
  type ReactNode,
} from "react";
import type { PublicEventSummary } from "../../types/api";

interface CaseContextValue {
  activeCase: PublicEventSummary | null;
  setActiveCase: (c: PublicEventSummary | null) => void;
  /** The canonical event_id UUID for the active case. */
  activeCaseId: string | null;
}

const CaseContext = createContext<CaseContextValue | null>(null);

export function CaseProvider({ children }: { children: ReactNode }) {
  const [activeCase, setActiveCaseState] = useState<PublicEventSummary | null>(
    null,
  );

  const setActiveCase = useCallback((c: PublicEventSummary | null) => {
    setActiveCaseState(c);
  }, []);

  return (
    <CaseContext.Provider
      value={{
        activeCase,
        setActiveCase,
        activeCaseId: null, // set from route params, not from switcher
      }}
    >
      {children}
    </CaseContext.Provider>
  );
}

export function useCaseContext(): CaseContextValue {
  const ctx = useContext(CaseContext);
  if (!ctx) throw new Error("useCaseContext must be used within CaseProvider");
  return ctx;
}
