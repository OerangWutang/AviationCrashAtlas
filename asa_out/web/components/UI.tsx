import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from "react";

type Tone = "neutral" | "blue" | "green" | "amber" | "red" | "purple";

export function cx(
  ...classes: Array<string | false | null | undefined>
): string {
  return classes.filter(Boolean).join(" ");
}

export function Card({
  children,
  className,
  as: Component = "div",
}: HTMLAttributes<HTMLDivElement> & {
  children: ReactNode;
  as?: "div" | "section" | "article";
}) {
  return (
    <Component
      className={cx(
        "rounded-2xl border border-stone-200 bg-white shadow-sm shadow-stone-200/40",
        className,
      )}
    >
      {children}
    </Component>
  );
}

export function Panel({
  children,
  className,
}: HTMLAttributes<HTMLDivElement> & { children: ReactNode }) {
  return (
    <section
      className={cx(
        "rounded-2xl border border-stone-200 bg-white p-4 shadow-sm shadow-stone-200/40 sm:p-5",
        className,
      )}
    >
      {children}
    </section>
  );
}

export function SectionHeader({
  eyebrow,
  title,
  description,
  actions,
  className,
}: {
  eyebrow?: string;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cx(
        "mb-4 flex flex-col gap-3 border-b border-stone-100 pb-3 sm:flex-row sm:items-end sm:justify-between",
        className,
      )}
    >
      <div className="min-w-0">
        {eyebrow && (
          <div className="mb-1 text-[10px] uppercase tracking-[0.18em] text-stone-400 font-mono">
            {eyebrow}
          </div>
        )}
        <h2
          className="text-[18px] leading-tight text-stone-900 sm:text-[20px]"
          style={{ fontFamily: "var(--ff-serif)" }}
        >
          {title}
        </h2>
        {description && (
          <p className="mt-1 max-w-2xl text-[12px] leading-relaxed text-stone-500">
            {description}
          </p>
        )}
      </div>
      {actions && (
        <div className="flex flex-wrap items-center gap-2">{actions}</div>
      )}
    </div>
  );
}

export function MetricCard({
  label,
  value,
  sub,
  tone = "neutral",
  className,
}: {
  label: ReactNode;
  value: ReactNode;
  sub?: ReactNode;
  tone?: Tone;
  className?: string;
}) {
  const toneClass: Record<Tone, string> = {
    neutral: "text-stone-900",
    blue: "text-blue-700",
    green: "text-emerald-700",
    amber: "text-amber-700",
    red: "text-red-700",
    purple: "text-violet-700",
  };
  return (
    <div
      className={cx(
        "rounded-xl border border-stone-200 bg-white p-4 shadow-sm shadow-stone-200/40",
        className,
      )}
    >
      <div className="text-[10px] uppercase tracking-[0.16em] text-stone-400 font-mono">
        {label}
      </div>
      <div
        className={cx(
          "mt-1 text-[24px] font-semibold tabular-nums leading-none font-mono",
          toneClass[tone],
        )}
      >
        {value}
      </div>
      {sub && (
        <div className="mt-2 text-[11px] leading-snug text-stone-400">
          {sub}
        </div>
      )}
    </div>
  );
}

export function EmptyState({
  icon = "✈",
  title,
  description,
  action,
  className,
}: {
  icon?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cx(
        "flex flex-col items-center justify-center rounded-2xl border border-dashed border-stone-200 bg-stone-50/70 px-6 py-10 text-center",
        className,
      )}
    >
      <div
        className="mb-3 flex h-11 w-11 items-center justify-center rounded-full bg-white text-[22px] shadow-sm shadow-stone-200/70"
        aria-hidden="true"
      >
        {icon}
      </div>
      <div className="text-[15px] font-medium text-stone-700">{title}</div>
      {description && (
        <div className="mt-1 max-w-sm text-[12px] leading-relaxed text-stone-400">
          {description}
        </div>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function LoadingState({
  label = "Loading…",
  rows = 4,
  className,
}: {
  label?: string;
  rows?: number;
  className?: string;
}) {
  return (
    <div
      className={cx(
        "rounded-2xl border border-stone-200 bg-white p-4 shadow-sm shadow-stone-200/40",
        className,
      )}
      aria-live="polite"
      aria-busy="true"
    >
      <div className="mb-4 flex items-center gap-2 text-[11px] uppercase tracking-[0.16em] text-stone-400 font-mono">
        <span className="h-3 w-3 rounded-full border-2 border-stone-200 border-t-[#185FA5] animate-spin" />
        {label}
      </div>
      <div className="space-y-3">
        {Array.from({ length: rows }).map((_, i) => (
          <div key={i} className="space-y-2">
            <div className="skeleton h-3 w-1/3 rounded" />
            <div className="skeleton h-4 w-full rounded" />
          </div>
        ))}
      </div>
    </div>
  );
}

export function StatusBadge({
  children,
  tone = "neutral",
  className,
}: {
  children: ReactNode;
  tone?: Tone;
  className?: string;
}) {
  const toneClass: Record<Tone, string> = {
    neutral: "border-stone-200 bg-stone-50 text-stone-600",
    blue: "border-blue-200 bg-blue-50 text-blue-700",
    green: "border-emerald-200 bg-emerald-50 text-emerald-700",
    amber: "border-amber-200 bg-amber-50 text-amber-700",
    red: "border-red-200 bg-red-50 text-red-700",
    purple: "border-violet-200 bg-violet-50 text-violet-700",
  };
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[10px] font-medium leading-none font-mono",
        toneClass[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

export function IconButton({
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...props}
      className={cx(
        "inline-flex min-h-9 items-center justify-center rounded-lg border border-stone-200 bg-white px-3 text-[12px] text-stone-600 shadow-sm transition hover:bg-stone-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-40 font-mono",
        className,
      )}
    />
  );
}
