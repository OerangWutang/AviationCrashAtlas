import { useState, useEffect } from "react";
import Head from "next/head";
import { useRouter } from "next/router";
import type { AccidentSummary, SearchFilters } from "../types";
import Header from "../components/Header";
import SearchSidebar from "../components/SearchSidebar";
import AccidentDetailPanel from "../components/AccidentDetailPanel";
import {
  useAccidentSearch,
  useAccidentDetail,
  useProvenance,
} from "../hooks/useAccidents";
import { useReviewerAuth } from "../hooks/useReviewerAuth";

const DEFAULT_FILTERS: SearchFilters = {
  q: "",
  severity: "",
  phase: "",
  year_from: "",
  min_source_completeness: "",
  fatality_status: "",
  registration: "",
  aircraft_type: "",
  operator: "",
  source_id: "",
  disputed_only: false,
  final_report_only: false,
  sort: "date_desc",
};

type DetailTab = "overview" | "technical";

function detailTabFromQuery(tab: string | string[] | undefined): DetailTab {
  return tab === "technical" ? "technical" : "overview";
}

function selectedIdFromQuery(
  selected: string | string[] | undefined,
  legacyId: string | string[] | undefined,
): string | null {
  if (typeof selected === "string" && selected.length > 0) return selected;
  // Backward-compatible alias for any old /?id=... links that may still exist.
  if (typeof legacyId === "string" && legacyId.length > 0) return legacyId;
  return null;
}

export default function SearchPage() {
  const router = useRouter();
  const [filters, setFilters] = useState<SearchFilters>(DEFAULT_FILTERS);
  const [page, setPage] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const reviewerAuth = useReviewerAuth();

  // Deep links from the map and conflict queue use ?selected=...; keep ?id=...
  // as a legacy alias so old review links do not strand users on an empty page.
  useEffect(() => {
    const id = selectedIdFromQuery(router.query.selected, router.query.id);
    if (id) setSelectedId(id);
  }, [router.query.selected, router.query.id]);

  const initialDetailTab = detailTabFromQuery(router.query.tab);

  const { results, total, hasNext, loading, error } = useAccidentSearch(
    filters,
    page,
  );
  const { detail, loading: detailLoading } = useAccidentDetail(selectedId);
  const {
    provenance,
    loading: provLoading,
    refresh: refreshProvenance,
  } = useProvenance(selectedId);

  const handleFiltersChange = (f: SearchFilters) => {
    setFilters(f);
    setPage(0);
  };
  const handleSelect = (a: AccidentSummary) => {
    setSelectedId(a.id);
  };

  return (
    <>
      <Head>
        <title>Aviation Safety Atlas — Search</title>
        <meta
          name="description"
          content="Search aviation accidents with source provenance and source completeness scoring"
        />
      </Head>
      <div className="flex flex-col h-screen overflow-hidden">
        <Header
          reviewerApiKey={reviewerAuth.apiKey}
          onReviewerApiKeyChange={reviewerAuth.setApiKey}
        />

        {error && (
          <div
            className="bg-red-50 border-b border-red-200 px-4 py-2 text-[11px] text-red-700 text-center"
            style={{ fontFamily: "var(--ff-mono)" }}
          >
            ✗ {error}
          </div>
        )}

        <div className="flex flex-1 flex-col overflow-hidden md:flex-row">
          <SearchSidebar
            results={results}
            total={total}
            page={page}
            hasNext={hasNext}
            loading={loading}
            selectedId={selectedId}
            filters={filters}
            onFiltersChange={handleFiltersChange}
            onSelect={handleSelect}
            onPageChange={setPage}
          />
          <main className="min-h-0 flex-1 overflow-hidden bg-stone-50/50">
            {detailLoading ? (
              <div className="flex items-center justify-center h-full">
                <div className="flex flex-col items-center gap-3 text-stone-400">
                  <div className="w-5 h-5 border-2 border-stone-200 border-t-[#185FA5] rounded-full animate-spin" />
                  <span
                    className="text-[12px]"
                    style={{ fontFamily: "var(--ff-mono)" }}
                  >
                    loading record…
                  </span>
                </div>
              </div>
            ) : detail ? (
              <AccidentDetailPanel
                accident={detail}
                provenance={provenance}
                loadingProvenance={provLoading}
                onProvenanceRefresh={refreshProvenance}
                initialTab={initialDetailTab}
                apiKey={reviewerAuth.apiKey || undefined}
              />
            ) : (
              <div className="flex h-full flex-col items-center justify-center px-8 text-center">
                <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl border border-blue-100 bg-white text-4xl shadow-sm shadow-stone-200">
                  ✈️
                </div>
                <h2
                  className="mb-2 text-[24px] text-stone-800"
                  style={{ fontFamily: "var(--ff-serif)" }}
                >
                  Select a safety record
                </h2>
                <p className="max-w-sm text-[13px] leading-relaxed text-stone-500">
                  Choose an accident from the explorer to inspect key facts,
                  source completeness, field provenance, flight path, weather,
                  failures, and related cases.
                </p>
              </div>
            )}
          </main>
        </div>
      </div>
    </>
  );
}
