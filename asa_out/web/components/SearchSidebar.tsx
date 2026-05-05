import { useMemo, useState, type ChangeEvent, type ReactNode } from "react";
import type { AccidentSummary, SearchFilters } from "../types";
import { PHASES } from "../lib/utils";
import ResultCard from "./ResultCard";
import { EmptyState, IconButton, StatusBadge, cx } from "./UI";

interface Props {
  results: AccidentSummary[];
  total: number;
  page: number;
  hasNext: boolean;
  loading: boolean;
  selectedId: string | null;
  filters: SearchFilters;
  onFiltersChange: (f: SearchFilters) => void;
  onSelect: (a: AccidentSummary) => void;
  onPageChange: (p: number) => void;
}

const CONTROL_CLASS =
  "w-full min-h-10 rounded-lg border border-stone-200 bg-white px-3 text-[13px] text-stone-700 shadow-sm placeholder:text-stone-300 transition focus:border-blue-300 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-100";

export default function SearchSidebar({
  results,
  total,
  page,
  hasNext,
  loading,
  selectedId,
  filters,
  onFiltersChange,
  onSelect,
  onPageChange,
}: Props) {
  const [filtersOpen, setFiltersOpen] = useState(false);
  const set =
    (key: keyof SearchFilters) =>
    (e: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
      onFiltersChange({ ...filters, [key]: e.target.value });
    };
  const setBool =
    (key: keyof SearchFilters) => (e: ChangeEvent<HTMLInputElement>) => {
      onFiltersChange({ ...filters, [key]: e.target.checked });
    };
  const activeFilterCount = useMemo(
    () =>
      Object.entries(filters).filter(([key, value]) => {
        if (key === "sort") return value !== "date_desc";
        if (typeof value === "boolean") return value;
        return value != null && value !== "";
      }).length,
    [filters],
  );
  const resetFilters = () =>
    onFiltersChange({
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
    });

  return (
    <aside
      className="flex max-h-[56vh] w-full flex-shrink-0 flex-col border-b border-stone-200 bg-stone-50/70 md:max-h-none md:w-80 md:border-b-0 md:border-r lg:w-[22rem]"
      aria-label="Accident search and filters"
    >
      <div className="border-b border-stone-200 bg-white p-4">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div>
            <div className="text-[10px] uppercase tracking-[0.18em] text-stone-400 font-mono">
              Search records
            </div>
            <div className="mt-1 text-[12px] text-stone-500">
              Find accidents by aircraft, operator, source, or location.
            </div>
          </div>
          <button
            type="button"
            onClick={() => setFiltersOpen((v) => !v)}
            className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-stone-200 bg-stone-50 px-3 text-[11px] text-stone-600 transition hover:bg-white focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 md:hidden font-mono"
            aria-expanded={filtersOpen}
            aria-controls="search-filter-panel"
          >
            Filters
            {activeFilterCount > 0 && (
              <StatusBadge tone="blue" className="px-1.5 py-0.5">
                {activeFilterCount}
              </StatusBadge>
            )}
          </button>
        </div>
        <label htmlFor="sidebar-search" className="sr-only">
          Search accident records
        </label>
        <div className="relative">
          <svg
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-stone-300"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            aria-hidden="true"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
            />
          </svg>
          <input
            id="sidebar-search"
            type="search"
            value={filters.q}
            onChange={set("q")}
            placeholder="Aircraft, location, operator…"
            className={cx(CONTROL_CLASS, "pl-10")}
          />
        </div>
      </div>

      <div
        id="search-filter-panel"
        className={cx(
          "border-b border-stone-200 bg-white p-4",
          filtersOpen ? "block" : "hidden md:block",
        )}
      >
        <div className="mb-3 flex items-center justify-between gap-3">
          <div className="text-[10px] uppercase tracking-[0.18em] text-stone-400 font-mono">
            Filters
          </div>
          {activeFilterCount > 0 && (
            <button
              type="button"
              onClick={resetFilters}
              className="rounded-md px-2 py-1 text-[11px] text-stone-500 transition hover:bg-stone-50 hover:text-stone-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 font-mono"
            >
              Clear {activeFilterCount}
            </button>
          )}
        </div>
        <div className="space-y-4">
          <FilterGroup title="Incident profile">
            <FilterRow label="Severity" htmlFor="severity-filter">
              <select
                id="severity-filter"
                value={filters.severity}
                onChange={set("severity")}
                className={CONTROL_CLASS}
              >
                <option value="">All severities</option>
                <option value="FATAL">Fatal</option>
                <option value="SERIOUS">Serious injury</option>
                <option value="MINOR">Minor injury</option>
                <option value="NONE">No injury</option>
              </select>
            </FilterRow>
            <FilterRow label="Phase of flight" htmlFor="phase-filter">
              <select
                id="phase-filter"
                value={filters.phase}
                onChange={set("phase")}
                className={CONTROL_CLASS}
              >
                <option value="">All phases</option>
                {PHASES.map((p) => (
                  <option key={p} value={p}>
                    {p.charAt(0) + p.slice(1).toLowerCase()}
                  </option>
                ))}
              </select>
            </FilterRow>
            <div className="grid grid-cols-2 gap-3">
              <FilterRow label="Year from" htmlFor="year-from-filter">
                <input
                  id="year-from-filter"
                  type="number"
                  value={filters.year_from}
                  onChange={set("year_from")}
                  placeholder="2015"
                  min={1962}
                  max={2100}
                  className={CONTROL_CLASS}
                />
              </FilterRow>
              <FilterRow label="Fatalities" htmlFor="fatality-filter">
                <select
                  id="fatality-filter"
                  value={filters.fatality_status}
                  onChange={set("fatality_status")}
                  className={CONTROL_CLASS}
                >
                  <option value="">Any</option>
                  <option value="some">Some</option>
                  <option value="none">Confirmed none</option>
                  <option value="unknown">Unknown</option>
                </select>
              </FilterRow>
            </div>
          </FilterGroup>
          <FilterGroup title="Aircraft and source">
            <FilterRow label="Registration" htmlFor="registration-filter">
              <input
                id="registration-filter"
                value={filters.registration ?? ""}
                onChange={set("registration")}
                placeholder="N123AB"
                className={CONTROL_CLASS}
              />
            </FilterRow>
            <FilterRow label="Aircraft type" htmlFor="aircraft-filter">
              <input
                id="aircraft-filter"
                value={filters.aircraft_type ?? ""}
                onChange={set("aircraft_type")}
                placeholder="Make or model"
                className={CONTROL_CLASS}
              />
            </FilterRow>
            <FilterRow label="Operator" htmlFor="operator-filter">
              <input
                id="operator-filter"
                value={filters.operator ?? ""}
                onChange={set("operator")}
                placeholder="Operator name"
                className={CONTROL_CLASS}
              />
            </FilterRow>
            <FilterRow label="Source ID" htmlFor="source-filter">
              <input
                id="source-filter"
                value={filters.source_id ?? ""}
                onChange={set("source_id")}
                placeholder="src-ntsb-001"
                className={CONTROL_CLASS}
              />
            </FilterRow>
          </FilterGroup>
          <FilterGroup title="Evidence controls">
            <FilterRow label="Min completeness" htmlFor="completeness-filter">
              <select
                id="completeness-filter"
                value={filters.min_source_completeness}
                onChange={set("min_source_completeness")}
                className={CONTROL_CLASS}
              >
                <option value="">Any source completeness</option>
                <option value="0.7">Mostly sourced (0.70+)</option>
                <option value="0.9">Well sourced (0.90+)</option>
              </select>
            </FilterRow>
            <label className="flex min-h-10 items-center gap-3 rounded-lg border border-stone-200 bg-stone-50 px-3 text-[12px] text-stone-600">
              <input
                type="checkbox"
                checked={!!filters.disputed_only}
                onChange={setBool("disputed_only")}
                className="h-4 w-4 rounded border-stone-300 text-blue-600 focus:ring-blue-400"
              />
              Only records with open disputes
            </label>
            <label className="flex min-h-10 items-center gap-3 rounded-lg border border-stone-200 bg-stone-50 px-3 text-[12px] text-stone-600">
              <input
                type="checkbox"
                checked={!!filters.final_report_only}
                onChange={setBool("final_report_only")}
                className="h-4 w-4 rounded border-stone-300 text-blue-600 focus:ring-blue-400"
              />
              Verified final-report records
            </label>
            <FilterRow label="Sort by" htmlFor="sort-filter">
              <select
                id="sort-filter"
                value={filters.sort}
                onChange={set("sort")}
                className={CONTROL_CLASS}
              >
                <option value="date_desc">Date (newest first)</option>
                <option value="date_asc">Date (oldest first)</option>
                <option value="source_completeness_desc">
                  Source completeness (highest)
                </option>
                <option value="fatalities_desc">Fatalities (most)</option>
              </select>
            </FilterRow>
          </FilterGroup>
        </div>
      </div>

      <div className="flex items-center justify-between gap-3 border-b border-stone-200 bg-stone-50 px-4 py-2 font-mono text-[11px] text-stone-500">
        <span>
          {loading
            ? "Loading records…"
            : `${total.toLocaleString()} record${total !== 1 ? "s" : ""}`}
        </span>
        {activeFilterCount > 0 && (
          <span>
            {activeFilterCount} active filter
            {activeFilterCount !== 1 ? "s" : ""}
          </span>
        )}
      </div>
      <div
        className="flex-1 overflow-y-auto sidebar-scroll bg-white"
        aria-live="polite"
      >
        {loading ? (
          <div className="p-4 space-y-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="rounded-xl border border-stone-100 p-3">
                <div className="skeleton mb-2 h-3 w-1/3" />
                <div className="skeleton mb-2 h-4 w-full" />
                <div className="skeleton h-3 w-2/3" />
              </div>
            ))}
          </div>
        ) : results.length === 0 ? (
          <div className="p-4">
            <EmptyState
              icon="⌕"
              title="No matching records"
              description="Try broadening the search text or clearing one of the evidence filters."
              action={
                activeFilterCount > 0 ? (
                  <IconButton type="button" onClick={resetFilters}>
                    Clear filters
                  </IconButton>
                ) : undefined
              }
            />
          </div>
        ) : (
          <div className="divide-y divide-stone-100 p-2">
            {results.map((a) => (
              <ResultCard
                key={a.id}
                accident={a}
                selected={selectedId === a.id}
                onClick={() => onSelect(a)}
              />
            ))}
          </div>
        )}
      </div>
      <div className="flex items-center gap-2 border-t border-stone-200 bg-white px-3 py-3">
        <IconButton
          type="button"
          disabled={page === 0}
          onClick={() => onPageChange(page - 1)}
          aria-label="Previous page"
          className="min-w-12"
        >
          ←
        </IconButton>
        <span className="flex-1 text-center text-[11px] text-stone-500 font-mono">
          Page {page + 1}
        </span>
        <IconButton
          type="button"
          disabled={!hasNext}
          onClick={() => onPageChange(page + 1)}
          aria-label="Next page"
          className="min-w-12"
        >
          →
        </IconButton>
      </div>
    </aside>
  );
}

function FilterGroup({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <fieldset className="space-y-3 rounded-xl border border-stone-200 bg-stone-50/70 p-3">
      <legend className="px-1 text-[10px] uppercase tracking-[0.16em] text-stone-400 font-mono">
        {title}
      </legend>
      {children}
    </fieldset>
  );
}

function FilterRow({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor: string;
  children: ReactNode;
}) {
  return (
    <div>
      <label
        htmlFor={htmlFor}
        className="mb-1.5 block text-[11px] font-medium text-stone-500"
      >
        {label}
      </label>
      {children}
    </div>
  );
}
