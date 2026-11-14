// Small form/layout primitives in shadcn style (theme tokens from styles/globals.css).
import * as React from 'react';
import { cn } from '@/lib/shadcn/utils';

const field =
  'border-input bg-background dark:bg-input/30 placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/40 w-full rounded-md border px-3 py-2 text-sm shadow-xs transition-[color,box-shadow] outline-none focus-visible:ring-[3px] disabled:opacity-50';

export function Input({ className, ...props }: React.ComponentProps<'input'>) {
  return <input className={cn(field, 'h-9', className)} {...props} />;
}

export function Textarea({ className, ...props }: React.ComponentProps<'textarea'>) {
  return <textarea className={cn(field, 'min-h-24', className)} {...props} />;
}

export function Field({
  label,
  hint,
  children,
  className,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <label className={cn('flex flex-col gap-2', className)}>
      <span className="text-sm leading-none font-medium">{label}</span>
      {children}
      {hint && <span className="text-muted-foreground text-xs">{hint}</span>}
    </label>
  );
}

export function Card({ className, ...props }: React.ComponentProps<'section'>) {
  return (
    <section
      className={cn('bg-card text-card-foreground rounded-xl border p-5 shadow-xs', className)}
      {...props}
    />
  );
}

/** Soft tinted pill; `busy` tones get a pulsing dot. */
const tones = {
  neutral: 'border-border bg-muted text-muted-foreground',
  brand: 'border-primary/20 bg-primary/10 text-primary',
  busy: 'border-sky-500/20 bg-sky-500/10 text-sky-700 dark:text-sky-300',
  good: 'border-emerald-500/20 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300',
  warn: 'border-amber-500/25 bg-amber-500/10 text-amber-700 dark:text-amber-300',
  bad: 'border-red-500/20 bg-red-500/10 text-red-700 dark:text-red-300',
};

export type Tone = keyof typeof tones;

export function Badge({
  tone = 'neutral',
  dot,
  children,
}: {
  tone?: Tone;
  dot?: boolean;
  children: React.ReactNode;
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap',
        tones[tone]
      )}
    >
      {dot && (
        <span className="relative flex size-1.5">
          {tone === 'busy' && (
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-current opacity-60" />
          )}
          <span className="relative inline-flex size-1.5 rounded-full bg-current" />
        </span>
      )}
      {children}
    </span>
  );
}

const STATUS_TONE: Record<string, Tone> = {
  ready: 'good',
  scored: 'good',
  completed: 'good',
  failed: 'bad',
  expired: 'warn',
  declined: 'warn',
  duplicate: 'warn',
  stale: 'warn',
  uploading: 'busy',
  queued: 'busy',
  parsing: 'busy',
  parsed: 'busy',
  embedding: 'busy',
  generating: 'busy',
  scoring: 'busy',
  active: 'busy',
  created: 'brand',
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <Badge tone={STATUS_TONE[status] ?? 'neutral'} dot>
      {status.charAt(0).toUpperCase() + status.slice(1)}
    </Badge>
  );
}

/** shadcn-style segmented tabs. */
export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
  className,
}: {
  tabs: { id: T; label: React.ReactNode }[];
  value: T;
  onChange: (id: T) => void;
  className?: string;
}) {
  return (
    <div
      role="tablist"
      className={cn(
        'bg-muted text-muted-foreground inline-flex h-9 w-fit max-w-full items-center gap-0.5 overflow-x-auto rounded-lg p-[3px]',
        className
      )}
    >
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={value === t.id}
          onClick={() => onChange(t.id)}
          className={cn(
            'inline-flex h-full flex-1 cursor-pointer items-center justify-center gap-1.5 rounded-md px-3 text-sm font-medium whitespace-nowrap transition-all',
            value === t.id
              ? 'bg-background text-foreground dark:bg-input/40 shadow-sm'
              : 'hover:text-foreground'
          )}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

/** Small count pill used inside tab labels. */
export function Count({ n }: { n: number }) {
  return (
    <span className="bg-foreground/10 rounded px-1.5 font-mono text-[10px] tabular-nums">{n}</span>
  );
}

/** Eyebrow, title, subtitle; `actions` sit on the right. */
export function PageHeader({
  eyebrow,
  title,
  subtitle,
  actions,
}: {
  eyebrow?: React.ReactNode;
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <header className="animate-rise flex flex-wrap items-end justify-between gap-4">
      <div className="flex min-w-0 flex-col gap-1.5">
        {eyebrow && (
          <div className="text-primary flex items-center gap-2 text-xs font-semibold tracking-wide uppercase">
            {eyebrow}
          </div>
        )}
        <h1 className="text-3xl leading-tight font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="text-muted-foreground text-sm">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

const LOGO_BARS = [5, 11, 15, 9];

/** Animated brand mark: a voice waveform in a rounded square. */
export function Logo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden className={cn('size-8', className)}>
      <defs>
        <linearGradient id="guru-logo" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="oklch(0.66 0.23 295)" />
          <stop offset="1" stopColor="oklch(0.45 0.26 283)" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="9" fill="url(#guru-logo)" />
      {LOGO_BARS.map((h, i) => (
        <rect
          key={i}
          x={8 + i * 4.6}
          y={16 - h / 2}
          width="2.6"
          height={h}
          rx="1.3"
          fill="white"
          className="animate-eq origin-center [transform-box:fill-box]"
          style={{ animationDelay: `${i * 0.18}s` }}
        />
      ))}
    </svg>
  );
}

/** Initials in a tinted circle; hue is stable per name. */
export function Avatar({ name, className }: { name: string; className?: string }) {
  const hue = [...name].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);
  const initials =
    name
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((w) => w[0]!.toUpperCase())
      .join('') || '?';
  return (
    <span
      aria-hidden
      className={cn(
        'grid size-9 shrink-0 place-items-center rounded-full text-xs font-semibold',
        'bg-[oklch(0.92_0.06_var(--h))] text-[oklch(0.4_0.15_var(--h))]',
        'dark:bg-[oklch(0.33_0.08_var(--h))] dark:text-[oklch(0.88_0.08_var(--h))]',
        className
      )}
      style={{ ['--h' as string]: hue }}
    >
      {initials}
    </span>
  );
}

function scoreColor(v: number) {
  return v >= 3.5
    ? 'oklch(0.66 0.16 155)'
    : v >= 2.5
      ? 'oklch(0.76 0.16 75)'
      : 'oklch(0.64 0.22 25)';
}

/** Circular 0–5 score gauge that draws itself in. */
export function ScoreRing({ value, size = 56 }: { value: number | null; size?: number }) {
  const r = 15.9155; // circumference = 100, so dash lengths are percentages
  const pct = value == null ? 0 : (value / 5) * 100;
  return (
    <svg
      viewBox="0 0 36 36"
      width={size}
      height={size}
      className="shrink-0"
      role="img"
      aria-label={value == null ? 'No score' : `${value.toFixed(2)} out of 5`}
    >
      <circle cx="18" cy="18" r={r} fill="none" stroke="var(--muted)" strokeWidth="3.2" />
      {value != null && (
        <circle
          cx="18"
          cy="18"
          r={r}
          fill="none"
          stroke={scoreColor(value)}
          strokeWidth="3.2"
          strokeLinecap="round"
          strokeDasharray={`${pct} 100`}
          transform="rotate(-90 18 18)"
          className="animate-draw"
          style={{ ['--dash' as string]: pct }}
        />
      )}
      <text
        x="18"
        y="18"
        dy="0.35em"
        textAnchor="middle"
        className="fill-foreground font-semibold tabular-nums"
        fontSize="9.5"
      >
        {value == null ? '—' : value.toFixed(1)}
      </text>
    </svg>
  );
}

/** Horizontal 0–5 bar that grows in. */
export function ScoreBar({ value }: { value: number }) {
  return (
    <div className="bg-muted h-1.5 w-full overflow-hidden rounded-full">
      <div
        className="animate-in slide-in-from-left h-full rounded-full duration-700"
        style={{ width: `${(value / 5) * 100}%`, background: scoreColor(value) }}
      />
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('skeleton rounded-md', className)} />;
}

/** Empty state with a floating illustration. */
export function EmptyState({
  title,
  body,
  children,
  className,
}: {
  title: string;
  body?: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        'animate-rise flex flex-col items-center gap-2 rounded-xl border border-dashed px-6 py-10 text-center',
        className
      )}
    >
      <svg viewBox="0 0 120 92" className="animate-float mb-2 h-20 w-auto" aria-hidden>
        <ellipse cx="60" cy="86" rx="32" ry="4" fill="var(--muted)" />
        <rect
          x="22"
          y="16"
          width="72"
          height="54"
          rx="10"
          fill="var(--card)"
          stroke="var(--border)"
          strokeWidth="1.5"
        />
        <rect x="32" y="28" width="28" height="5" rx="2.5" fill="var(--primary)" opacity="0.85" />
        <rect x="32" y="39" width="50" height="4" rx="2" fill="var(--muted)" />
        <rect x="32" y="48" width="38" height="4" rx="2" fill="var(--muted)" />
        <rect x="32" y="57" width="44" height="4" rx="2" fill="var(--muted)" />
        <circle cx="92" cy="20" r="11" fill="var(--primary)" />
        <path
          d="M87 20h10M92 15v10"
          stroke="var(--primary-foreground)"
          strokeWidth="2"
          strokeLinecap="round"
        />
      </svg>
      <p className="font-medium">{title}</p>
      {body && <p className="text-muted-foreground max-w-sm text-sm">{body}</p>}
      {children}
    </div>
  );
}

/** Small uppercase section label. */
export function SectionTitle({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <h2
      className={cn(
        'text-muted-foreground text-xs font-semibold tracking-wider uppercase',
        className
      )}
    >
      {children}
    </h2>
  );
}

export function ErrorText({ children }: { children: React.ReactNode }) {
  if (!children) return null;
  return (
    <p
      role="alert"
      className="border-destructive/30 bg-destructive/5 text-destructive animate-rise rounded-lg border px-3 py-2 text-sm"
    >
      {children}
    </p>
  );
}

/** Re-runs `fn` every `ms` while `active` is true (ingest, plan, and scoring progress). */
export function usePoll(fn: () => void, active: boolean, ms = 2000) {
  const ref = React.useRef(fn);
  ref.current = fn;
  React.useEffect(() => {
    if (!active) return;
    const t = setInterval(() => ref.current(), ms);
    return () => clearInterval(t);
  }, [active, ms]);
}

export function formatDuration(sec: number | null | undefined) {
  if (sec == null) return '—';
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}
