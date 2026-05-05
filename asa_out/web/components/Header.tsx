import Link from "next/link";
import { useRouter } from "next/router";
import ReviewerAuthControl from "./ReviewerAuthControl";
import { StatusBadge, cx } from "./UI";

const IS_MOCK = process.env.NEXT_PUBLIC_USE_MOCK === "true";

const SOURCES = [
  { label: "NTSB", active: true },
  { label: "ASN", active: false },
  { label: "ICAO", active: false },
];

interface Props {
  reviewerApiKey?: string;
  onReviewerApiKeyChange?: (apiKey: string) => void;
}

export default function Header({
  reviewerApiKey,
  onReviewerApiKeyChange,
}: Props = {}) {
  const router = useRouter();
  const tabs = [
    { label: "Search", href: "/", description: "Record explorer" },
    { label: "Map", href: "/map", description: "Spatial view" },
    {
      label: "Analytics",
      href: "/analytics",
      description: "Operational metrics",
    },
    { label: "Sources", href: "/sources", description: "Ingestion status" },
    { label: "Conflicts", href: "/conflicts", description: "Review queue" },
    { label: "Operator", href: "/operator", description: "Admin tools" },
  ];

  return (
    <header className="relative z-20 border-b border-stone-200 bg-white/95 shadow-sm shadow-stone-200/60 backdrop-blur">
      <div className="flex flex-col gap-3 px-4 py-3 sm:px-5 lg:flex-row lg:items-center lg:gap-5">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <Link
            href="/"
            className="group inline-flex min-w-0 items-center gap-3 rounded-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 focus-visible:ring-offset-2"
            aria-label="Aviation Safety Atlas home"
          >
            <span className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-xl border border-blue-100 bg-gradient-to-br from-blue-50 to-stone-50 text-[17px] shadow-sm">
              ✈
            </span>
            <span className="min-w-0">
              <span
                className="block truncate text-[20px] leading-none text-stone-900"
                style={{ fontFamily: "var(--ff-serif)" }}
              >
                Aviation{" "}
                <span className="italic text-[#185FA5]">Safety Atlas</span>
              </span>
              <span className="mt-1 hidden text-[10px] uppercase tracking-[0.18em] text-stone-400 font-mono sm:block">
                v0.1.0-mvp · aviation intelligence dashboard
              </span>
            </span>
          </Link>
        </div>
        <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center sm:justify-between lg:justify-end">
          {onReviewerApiKeyChange && (
            <div className="min-w-0 sm:order-2">
              <ReviewerAuthControl
                apiKey={reviewerApiKey ?? ""}
                onApiKeyChange={onReviewerApiKeyChange}
                compact
              />
            </div>
          )}
          <div
            className="flex min-w-0 items-center gap-1.5 overflow-x-auto pb-0.5 sm:order-1 sm:pb-0"
            aria-label="Configured sources"
          >
            {SOURCES.map((s) => (
              <StatusBadge
                key={s.label}
                tone={s.active ? "blue" : "neutral"}
                className="flex-shrink-0"
              >
                <span
                  className={cx(
                    "h-1.5 w-1.5 rounded-full",
                    s.active ? "bg-blue-600" : "bg-stone-300",
                  )}
                  aria-hidden="true"
                />
                {s.label}
                {!s.active && (
                  <span className="text-stone-400">generic-csv</span>
                )}
              </StatusBadge>
            ))}
            {IS_MOCK && (
              <StatusBadge tone="amber" className="flex-shrink-0">
                Demo mode
              </StatusBadge>
            )}
          </div>
        </div>
      </div>
      <nav
        className="border-t border-stone-100 bg-stone-50/80"
        aria-label="Primary navigation"
      >
        <div className="flex gap-1 overflow-x-auto px-3 sm:px-5">
          {tabs.map((tab) => {
            const active = router.pathname === tab.href;
            return (
              <Link
                key={tab.href}
                href={tab.href}
                aria-current={active ? "page" : undefined}
                title={tab.description}
                className={cx(
                  "relative -mb-px inline-flex min-h-11 flex-shrink-0 items-center gap-2 border-b-2 px-3 text-[12px] transition focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 focus-visible:ring-offset-2 font-mono sm:px-4",
                  active
                    ? "border-[#185FA5] bg-white text-[#185FA5] shadow-[0_-1px_0_0_#fff_inset]"
                    : "border-transparent text-stone-500 hover:border-stone-200 hover:bg-white/70 hover:text-stone-800",
                )}
              >
                {tab.label}
              </Link>
            );
          })}
        </div>
      </nav>
      {IS_MOCK && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-amber-200 bg-amber-50 px-4 py-2 text-[11px] text-amber-800 sm:px-5 font-mono">
          <span className="font-semibold">Demo mode</span>
          <span>
            Displaying hardcoded sample data, not live aviation safety records.
          </span>
          <span className="text-amber-600">
            Set NEXT_PUBLIC_USE_MOCK=false and start the backend for real data.
          </span>
        </div>
      )}
    </header>
  );
}
