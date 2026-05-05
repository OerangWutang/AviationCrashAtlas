import Head from "next/head";
import { useEffect, useState, type ReactNode } from "react";
import Header from "../components/Header";
import {
  EmptyState,
  LoadingState,
  MetricCard,
  Panel,
  SectionHeader,
  StatusBadge,
} from "../components/UI";
import {
  fetchAdvancedSummary,
  fetchAnalyticsSummary,
  fetchDataQuality,
  fetchSystemFailurePatterns,
} from "../lib/api";
import { SEV_COLOR } from "../lib/utils";
import type {
  AdvancedSummary,
  AnalyticsSummary,
  DataQualitySummary,
  SystemFailurePatterns,
} from "../types";

const USE_MOCK = process.env.NEXT_PUBLIC_USE_MOCK === "true";
const CONF_LABELS = [
  "Weakly sourced",
  "Partially sourced",
  "Mostly sourced",
  "Well sourced",
];
const CONF_SUBLABELS = ["< 50%", "50–70%", "70–90%", "90%+"];

export default function AnalyticsPage() {
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [advanced, setAdvanced] = useState<AdvancedSummary | null>(null);
  const [dataQuality, setDataQuality] = useState<DataQualitySummary | null>(
    null,
  );
  const [sfPatterns, setSfPatterns] = useState<SystemFailurePatterns | null>(
    null,
  );

  useEffect(() => {
    if (USE_MOCK) {
      setError(
        "Analytics is not available in mock mode. Start the backend to view full-dataset statistics.",
      );
      setLoading(false);
      return;
    }

    let cancelled = false;
    fetchAnalyticsSummary()
      .then((data) => {
        if (!cancelled) {
          setSummary(data);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(String(err));
          setLoading(false);
        }
      });

    fetchAdvancedSummary()
      .then((d) => {
        if (!cancelled) setAdvanced(d);
      })
      .catch(() => undefined);
    fetchDataQuality()
      .then((d) => {
        if (!cancelled) setDataQuality(d);
      })
      .catch(() => undefined);
    fetchSystemFailurePatterns()
      .then((d) => {
        if (!cancelled) setSfPatterns(d);
      })
      .catch(() => undefined);

    return () => {
      cancelled = true;
    };
  }, []);

  const stats = summary
    ? {
        fatal: summary.fatal_count,
        totalFatalities: summary.total_fatalities,
        avgConf: summary.avg_source_completeness ?? summary.avg_confidence,
        byPhase: summary.by_phase,
        bySev: summary.by_severity,
        confBins: [
          (summary.source_completeness_bins ?? summary.confidence_bins)
            .weakly_sourced ?? 0,
          (summary.source_completeness_bins ?? summary.confidence_bins)
            .partially_sourced ?? 0,
          (summary.source_completeness_bins ?? summary.confidence_bins)
            .mostly_sourced ?? 0,
          (summary.source_completeness_bins ?? summary.confidence_bins)
            .well_sourced ?? 0,
        ],
        byYear: summary.by_year,
        totalAccidents: summary.total_accidents,
      }
    : null;

  if (error) {
    return (
      <>
        <Head>
          <title>Aviation Safety Atlas — Analytics</title>
        </Head>
        <div className="flex h-screen flex-col bg-stone-50">
          <Header />
          <main className="flex flex-1 items-center justify-center p-6">
            <Panel className="max-w-lg">
              <EmptyState
                icon="📊"
                title="Analytics unavailable"
                description={error}
              />
            </Panel>
          </main>
        </div>
      </>
    );
  }

  if (loading || !stats) {
    return (
      <>
        <Head>
          <title>Aviation Safety Atlas — Analytics</title>
        </Head>
        <div className="flex h-screen flex-col bg-stone-50">
          <Header />
          <main className="mx-auto w-full max-w-7xl flex-1 p-4 sm:p-6">
            <LoadingState label="Loading analytics dashboard…" rows={6} />
          </main>
        </div>
      </>
    );
  }

  const maxPhase = Math.max(...Object.values(stats.byPhase), 1);
  const maxConf = Math.max(...stats.confBins, 1);
  const fatalPct =
    stats.totalAccidents > 0
      ? ((stats.fatal / stats.totalAccidents) * 100).toFixed(0)
      : "0";
  const currentYear = new Date().getFullYear();
  const recentYears = Object.entries(stats.byYear)
    .map(([year, count]) => ({ year: Number(year), count }))
    .filter(({ year }) => year >= currentYear - 10)
    .sort((a, b) => a.year - b.year);
  const maxYear = Math.max(...recentYears.map((r) => r.count), 1);

  return (
    <>
      <Head>
        <title>Aviation Safety Atlas — Analytics</title>
      </Head>
      <div className="flex h-screen flex-col overflow-hidden bg-stone-50">
        <Header />
        <main className="flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-7xl px-4 py-5 sm:px-6 lg:px-8">
            <div className="mb-6 rounded-3xl border border-stone-200 bg-white p-5 shadow-sm shadow-stone-200/50 sm:p-6">
              <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
                <div>
                  <div className="mb-2 text-[10px] uppercase tracking-[0.2em] text-stone-400 font-mono">
                    Operational intelligence
                  </div>
                  <h1
                    className="text-[28px] leading-tight text-stone-900 sm:text-[34px]"
                    style={{ fontFamily: "var(--ff-serif)" }}
                  >
                    Analytics
                  </h1>
                  <p className="mt-2 max-w-3xl text-[13px] leading-relaxed text-stone-500">
                    Aggregate safety trends, source-completeness signals,
                    dispute volume, and system failure patterns for the current
                    accident dataset.
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <StatusBadge tone="blue">NTSB primary source</StatusBadge>
                  <StatusBadge
                    tone={
                      stats.avgConf >= 0.75
                        ? "green"
                        : stats.avgConf >= 0.5
                          ? "amber"
                          : "red"
                    }
                  >
                    {Math.round(stats.avgConf * 100)}% avg completeness
                  </StatusBadge>
                </div>
              </div>
            </div>

            <section
              className="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4"
              aria-label="Analytics summary metrics"
            >
              <MetricCard
                label="Total records"
                value={stats.totalAccidents.toLocaleString()}
                sub="dataset accidents"
              />
              <MetricCard
                label="Fatal accidents"
                value={stats.fatal.toLocaleString()}
                sub={`${fatalPct}% of records`}
                tone={stats.fatal > 0 ? "red" : "green"}
              />
              <MetricCard
                label="Total fatalities"
                value={stats.totalFatalities.toLocaleString()}
                sub="across all events"
                tone={stats.totalFatalities > 0 ? "red" : "neutral"}
              />
              <MetricCard
                label="Avg completeness"
                value={stats.avgConf.toFixed(2)}
                sub="claim-based source score"
                tone={
                  stats.avgConf >= 0.75
                    ? "green"
                    : stats.avgConf >= 0.5
                      ? "amber"
                      : "red"
                }
              />
            </section>

            <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
              <Panel>
                <SectionHeader
                  eyebrow="Distribution"
                  title="Accidents by phase of flight"
                  description="Counts by reported flight phase, sorted by frequency."
                />
                <div className="space-y-3">
                  {Object.entries(stats.byPhase)
                    .sort((a, b) => b[1] - a[1])
                    .map(([phase, count]) => (
                      <BarRow
                        key={phase}
                        label={phase}
                        value={count}
                        width={`${(count / maxPhase) * 100}%`}
                      />
                    ))}
                  {Object.keys(stats.byPhase).length === 0 && (
                    <EmptyState
                      title="No phase data"
                      description="Records do not contain phase-of-flight values yet."
                    />
                  )}
                </div>
              </Panel>

              <Panel>
                <SectionHeader
                  eyebrow="Quality"
                  title="Source completeness distribution"
                  description="Buckets reflect how much supporting source coverage exists for projected fields."
                />
                <div className="space-y-3">
                  {stats.confBins.map((count, i) => (
                    <BarRow
                      key={CONF_LABELS[i]}
                      label={
                        <span>
                          {CONF_LABELS[i]}{" "}
                          <span className="text-stone-400">
                            {CONF_SUBLABELS[i]}
                          </span>
                        </span>
                      }
                      value={count}
                      width={`${(count / maxConf) * 100}%`}
                    />
                  ))}
                </div>
              </Panel>
            </div>

            <div className="mt-6 grid grid-cols-1 gap-6 xl:grid-cols-3">
              <Panel className="xl:col-span-2">
                <SectionHeader
                  eyebrow="Severity"
                  title="Injury severity breakdown"
                  description="Severity labels are shown with text and count so status is not color-only."
                />
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {Object.entries(stats.bySev).map(([sev, count]) => (
                    <div
                      key={sev}
                      className="rounded-xl border border-stone-200 bg-stone-50 p-3"
                    >
                      <div className="flex items-center justify-between gap-3">
                        <span className="flex items-center gap-2 text-[12px] font-medium text-stone-700">
                          <span
                            className="h-2.5 w-2.5 rounded-full"
                            style={{ background: SEV_COLOR[sev] ?? "#78716c" }}
                            aria-hidden="true"
                          />
                          {sev || "Unknown"}
                        </span>
                        <span className="text-[13px] font-mono tabular-nums text-stone-900">
                          {count.toLocaleString()}
                        </span>
                      </div>
                      <div className="mt-2 text-[10px] text-stone-400 font-mono">
                        {stats.totalAccidents > 0
                          ? `${((count / stats.totalAccidents) * 100).toFixed(0)}% of records`
                          : "no records"}
                      </div>
                    </div>
                  ))}
                </div>
              </Panel>

              <Panel>
                <SectionHeader
                  eyebrow="Recent years"
                  title="Record volume"
                  description="Last ten years available in the aggregate response."
                />
                <div className="space-y-2">
                  {recentYears.length > 0 ? (
                    recentYears.map(({ year, count }) => (
                      <BarRow
                        key={year}
                        label={year}
                        value={count}
                        width={`${(count / maxYear) * 100}%`}
                        compact
                      />
                    ))
                  ) : (
                    <EmptyState
                      title="No recent-year data"
                      description="The aggregate response did not include recent year buckets."
                    />
                  )}
                </div>
              </Panel>
            </div>

            {advanced && (
              <Panel className="mt-6">
                <SectionHeader
                  eyebrow="Advanced"
                  title="Advanced overview"
                  description={advanced.computation_note}
                />
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                  <MetricCard
                    label="Fatal accidents"
                    value={advanced.fatal_accidents.toLocaleString()}
                    tone="red"
                  />
                  <MetricCard
                    label="Disputed records"
                    value={advanced.disputed_records.toLocaleString()}
                    tone={advanced.disputed_records > 0 ? "purple" : "green"}
                  />
                  <MetricCard
                    label="Low-confidence records"
                    value={advanced.low_confidence_records.toLocaleString()}
                    tone={
                      advanced.low_confidence_records > 0 ? "amber" : "green"
                    }
                  />
                </div>
              </Panel>
            )}

            <div className="mt-6 grid grid-cols-1 gap-6 xl:grid-cols-2">
              {dataQuality && (
                <Panel>
                  <SectionHeader
                    eyebrow="Data hygiene"
                    title="Data quality"
                    description={dataQuality.quality_note}
                  />
                  <div className="space-y-2">
                    {[
                      {
                        label: "Missing accident date",
                        value: dataQuality.missing_date,
                      },
                      {
                        label: "Missing location",
                        value: dataQuality.missing_location,
                      },
                      {
                        label: "Missing aircraft model",
                        value: dataQuality.missing_aircraft_model,
                      },
                      {
                        label: "Has field conflicts",
                        value: dataQuality.has_conflicts,
                      },
                      {
                        label: "Low confidence (< 50%)",
                        value: dataQuality.low_confidence_records,
                      },
                      {
                        label: "Single-source records",
                        value: dataQuality.single_source_records,
                      },
                      {
                        label: "Preliminary-only",
                        value: dataQuality.preliminary_only_records,
                      },
                    ].map(({ label, value }) => (
                      <QualityRow
                        key={label}
                        label={label}
                        value={value}
                        total={Math.max(dataQuality.total_records, 1)}
                      />
                    ))}
                  </div>
                </Panel>
              )}

              {sfPatterns && Object.keys(sfPatterns.by_category).length > 0 && (
                <Panel>
                  <SectionHeader
                    eyebrow="Mechanical intelligence"
                    title="System failure patterns"
                    description={sfPatterns.status_note}
                  />
                  <div className="mb-4 grid grid-cols-2 gap-3">
                    <MetricCard
                      label="Failure records"
                      value={sfPatterns.total_failure_records.toLocaleString()}
                    />
                    <MetricCard
                      label="Confirmed causal"
                      value={sfPatterns.confirmed_causal_count.toLocaleString()}
                      tone={
                        sfPatterns.confirmed_causal_count > 0
                          ? "red"
                          : "neutral"
                      }
                    />
                  </div>
                  <div className="space-y-2">
                    {Object.entries(sfPatterns.by_category)
                      .sort(
                        ([, a], [, b]) =>
                          (b.confirmed ?? 0) - (a.confirmed ?? 0),
                      )
                      .slice(0, 10)
                      .map(([cat, counts]) => (
                        <div
                          key={cat}
                          className="rounded-xl border border-stone-200 bg-stone-50 p-3"
                        >
                          <div className="mb-2 flex items-center justify-between gap-2">
                            <span className="text-[12px] font-medium capitalize text-stone-700">
                              {cat.replace(/_/g, " ")}
                            </span>
                            <span className="text-[10px] text-stone-400 font-mono">
                              {Object.values(counts).reduce(
                                (sum, n) => sum + n,
                                0,
                              )}{" "}
                              total
                            </span>
                          </div>
                          <div className="flex flex-wrap gap-1.5">
                            {counts.confirmed > 0 && (
                              <StatusBadge tone="red">
                                confirmed {counts.confirmed}
                              </StatusBadge>
                            )}
                            {counts.suspected > 0 && (
                              <StatusBadge tone="amber">
                                suspected {counts.suspected}
                              </StatusBadge>
                            )}
                            {counts.disputed > 0 && (
                              <StatusBadge tone="purple">
                                disputed {counts.disputed}
                              </StatusBadge>
                            )}
                            {counts.ruled_out > 0 && (
                              <StatusBadge tone="neutral">
                                ruled out {counts.ruled_out}
                              </StatusBadge>
                            )}
                          </div>
                        </div>
                      ))}
                  </div>
                </Panel>
              )}
            </div>

            <div className="py-8 text-center text-[11px] text-stone-400 font-mono">
              displaying {stats.totalAccidents.toLocaleString()} records · NTSB
              primary source · source-completeness-scored claim-based data
            </div>
          </div>
        </main>
      </div>
    </>
  );
}

function BarRow({
  label,
  value,
  width,
  compact = false,
}: {
  label: ReactNode;
  value: number;
  width: string;
  compact?: boolean;
}) {
  return (
    <div className="grid grid-cols-[minmax(86px,150px)_1fr_auto] items-center gap-3">
      <div
        className="truncate text-right text-[11px] text-stone-500 font-mono"
        title={String(label)}
      >
        {label}
      </div>
      <div
        className={
          compact
            ? "h-3 overflow-hidden rounded-full bg-stone-100"
            : "h-4 overflow-hidden rounded-full bg-stone-100"
        }
      >
        <div className="h-full rounded-full bg-[#185FA5]" style={{ width }} />
      </div>
      <div className="min-w-8 text-right text-[11px] tabular-nums text-stone-600 font-mono">
        {value.toLocaleString()}
      </div>
    </div>
  );
}

function QualityRow({
  label,
  value,
  total,
}: {
  label: string;
  value: number;
  total: number;
}) {
  const pct = (value / total) * 100;
  return (
    <div className="rounded-xl border border-stone-200 bg-stone-50 px-3 py-2">
      <div className="mb-1 flex items-center justify-between gap-3">
        <span className="text-[12px] text-stone-600">{label}</span>
        <span className="text-[11px] text-stone-800 font-mono">
          {value.toLocaleString()}{" "}
          <span className="text-stone-400">({pct.toFixed(1)}%)</span>
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-white">
        <div
          className="h-full rounded-full bg-[#185FA5]"
          style={{ width: `${Math.min(pct, 100)}%` }}
        />
      </div>
    </div>
  );
}
