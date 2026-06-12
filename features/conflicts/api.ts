/**
 * Conflicts API hooks.
 *
 * All requests go through the BFF.
 * - Conflict list: /api/cases/:slug/conflicts (slug-based, BFF resolves event_id)
 * - Conflict detail/candidates/history/resolve/reopen: /api/conflicts/:id (stable UUID)
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch, isApiError } from "../../lib/api";
import type {
  ConflictSummary,
  ConflictHistoryResponse,
  ConflictCandidatesResponse,
  ResolveRequest,
  ReopenRequest,
  ResolveResponse,
  ConflictModified409,
} from "../../types/api";
import { caseKeys } from "../cases/api";

export const conflictKeys = {
  all: ["conflicts"] as const,
  list: (eventId: string) =>
    [...conflictKeys.all, "list", eventId] as const,
  detail: (conflictId: string) =>
    [...conflictKeys.all, "detail", conflictId] as const,
  history: (conflictId: string) =>
    [...conflictKeys.all, "history", conflictId] as const,
  candidates: (conflictId: string) =>
    [...conflictKeys.all, "candidates", conflictId] as const,
};

/** List conflicts by case slug — used when you have the slug but not event_id. */
export function useConflictListBySlug(slug: string | undefined) {
  return useQuery({
    queryKey: [...caseKeys.detail(slug!), "conflicts"] as const,
    queryFn: () =>
      apiFetch<ConflictSummary[]>(`/api/cases/${slug}/conflicts?limit=200`),
    enabled: !!slug,
  });
}

export function useConflictDetail(conflictId: string | undefined) {
  return useQuery({
    queryKey: conflictKeys.detail(conflictId!),
    queryFn: () =>
      apiFetch<ConflictSummary>(`/api/conflicts/${conflictId}`),
    enabled: !!conflictId,
  });
}

export function useConflictHistory(conflictId: string | undefined) {
  return useQuery({
    queryKey: conflictKeys.history(conflictId!),
    queryFn: () =>
      apiFetch<ConflictHistoryResponse>(
        `/api/conflicts/${conflictId}/history?limit=50`,
      ),
    enabled: !!conflictId,
  });
}

/**
 * Load the full claim candidates for a conflict.
 *
 * The resolve form MUST submit `winning_claim_id = candidate.claimId` directly.
 * Never index into ConflictSummary.claimIds.
 */
export function useConflictCandidates(conflictId: string | undefined) {
  return useQuery({
    queryKey: conflictKeys.candidates(conflictId!),
    queryFn: () =>
      apiFetch<ConflictCandidatesResponse>(
        `/api/conflicts/${conflictId}/candidates`,
      ),
    enabled: !!conflictId,
  });
}

/**
 * Resolve mutation with optimistic update and 409 handling.
 */
export function useResolveConflict(eventId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      conflictId,
      payload,
    }: {
      conflictId: string;
      payload: ResolveRequest;
    }) =>
      apiFetch<ResolveResponse>(`/api/conflicts/${conflictId}/resolve`, {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: (data, { conflictId }) => {
      queryClient.setQueryData(conflictKeys.detail(conflictId), data.conflict);
      queryClient.invalidateQueries({ queryKey: conflictKeys.list(eventId) });
      queryClient.invalidateQueries({ queryKey: conflictKeys.history(conflictId) });
      queryClient.invalidateQueries({ queryKey: conflictKeys.candidates(conflictId) });
      queryClient.invalidateQueries({ queryKey: caseKeys.all });
    },
  });
}

export function useReopenConflict(eventId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      conflictId,
      payload,
    }: {
      conflictId: string;
      payload: ReopenRequest;
    }) =>
      apiFetch<ResolveResponse>(`/api/conflicts/${conflictId}/reopen`, {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: (data, { conflictId }) => {
      queryClient.setQueryData(conflictKeys.detail(conflictId), data.conflict);
      queryClient.invalidateQueries({ queryKey: conflictKeys.list(eventId) });
      queryClient.invalidateQueries({ queryKey: conflictKeys.history(conflictId) });
      queryClient.invalidateQueries({ queryKey: conflictKeys.candidates(conflictId) });
      queryClient.invalidateQueries({ queryKey: caseKeys.all });
    },
  });
}

/**
 * Parse a 409 error into a typed ConflictModified409.
 */
export function parse409(err: unknown): ConflictModified409 | null {
  if (!isApiError(err) || err.status !== 409) return null;
  try {
    const detail = err.detail as ConflictModified409;
    if (detail && typeof detail.currentVersion === "number") {
      return detail;
    }
  } catch {
    // malformed 409
  }
  return null;
}
