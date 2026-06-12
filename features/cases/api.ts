/**
 * Cases API hooks.
 *
 * All requests go through the BFF (/api/cases/*).
 * The BFF owns slug↔event_id resolution and proxies to Atlas FastAPI.
 * The browser never calls FastAPI directly.
 */

import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../../lib/api";
import type {
  PublicEventListResponse,
  PublicEventDetail,
  EvidenceResponse,
  FieldExplanationResponse,
  TimelineResponse,
  PageAuditResponse,
  ProvenanceResponse,
} from "../../types/api";

export const caseKeys = {
  all: ["cases"] as const,
  list: () => [...caseKeys.all, "list"] as const,
  detail: (slug: string) => [...caseKeys.all, "detail", slug] as const,
  evidence: (slug: string) => [...caseKeys.all, "evidence", slug] as const,
  timeline: (slug: string) => [...caseKeys.all, "timeline", slug] as const,
  audit: (slug: string) => [...caseKeys.all, "audit", slug] as const,
  fieldExplanation: (slug: string, fieldName: string) =>
    [...caseKeys.all, "audit", slug, "field", fieldName] as const,
  provenance: (slug: string) => [...caseKeys.all, "provenance", slug] as const,
};

export function useCaseList() {
  return useQuery({
    queryKey: caseKeys.list(),
    queryFn: () => apiFetch<PublicEventListResponse>("/api/cases?limit=100"),
  });
}

export function useCaseDetail(slug: string | undefined) {
  return useQuery({
    queryKey: caseKeys.detail(slug!),
    queryFn: () => apiFetch<PublicEventDetail>(`/api/cases/${slug}`),
    enabled: !!slug,
  });
}

export function useCaseEvidence(slug: string | undefined) {
  return useQuery({
    queryKey: caseKeys.evidence(slug!),
    queryFn: () => apiFetch<EvidenceResponse>(`/api/cases/${slug}/evidence`),
    enabled: !!slug,
  });
}

export function useCaseTimeline(slug: string | undefined) {
  return useQuery({
    queryKey: caseKeys.timeline(slug!),
    queryFn: () => apiFetch<TimelineResponse>(`/api/cases/${slug}/timeline`),
    enabled: !!slug,
  });
}

export function useCaseAudit(slug: string | undefined) {
  return useQuery({
    queryKey: caseKeys.audit(slug!),
    queryFn: () => apiFetch<PageAuditResponse>(`/api/cases/${slug}/audit`),
    enabled: !!slug,
  });
}

export function useFieldExplanation(
  slug: string | undefined,
  fieldName: string | null,
) {
  return useQuery({
    queryKey: caseKeys.fieldExplanation(slug!, fieldName!),
    queryFn: () =>
      apiFetch<FieldExplanationResponse>(
        `/api/cases/${slug}/audit/fields/${encodeURIComponent(fieldName!)}/explanation`,
      ),
    enabled: !!slug && !!fieldName,
  });
}

export function useCaseProvenance(slug: string | undefined) {
  return useQuery({
    queryKey: caseKeys.provenance(slug!),
    queryFn: () => apiFetch<ProvenanceResponse>(`/api/cases/${slug}/provenance?limit=200`),
    enabled: !!slug,
  });
}
