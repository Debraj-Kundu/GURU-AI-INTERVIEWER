// Small form/layout primitives in the starter's shadcn style (theme tokens from styles/globals.css).
import * as React from 'react';
import { cn } from '@/lib/shadcn/utils';

const field =
  'border-input bg-card placeholder:text-muted-foreground focus-visible:border-primary focus-visible:ring-primary/15 w-full rounded-lg border px-3 py-2 text-sm transition-colors outline-none focus-visible:ring-[3px] disabled:opacity-50';

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
    <label className={cn('flex flex-col gap-1.5', className)}>
      <span className="text-muted-foreground font-mono text-[11px] tracking-widest uppercase">
        {label}
      </span>
      {children}
      {hint && <span className="text-muted-foreground text-xs">{hint}</span>}
    </label>
  );
}

export function Card({ className, ...props }: React.ComponentProps<'section'>) {
  return <section className={cn('bg-card rounded-xl border p-5', className)} {...props} />;
}

// Solid fill, same-colour border, white text.
const tones = {
  neutral: 'border-zinc-500 bg-zinc-500',
  busy: 'border-sky-500 bg-sky-500',
  good: 'border-emerald-500 bg-emerald-500',
  warn: 'border-amber-500 bg-amber-500',
  bad: 'border-red-500 bg-red-500',
};

export type Tone = keyof typeof tones;

export function Badge({ tone = 'neutral', children }: { tone?: Tone; children: React.ReactNode }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 font-mono text-[10px] tracking-wider text-white uppercase',
        tones[tone]
      )}
    >
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
};

export function StatusBadge({ status }: { status: string }) {
  return <Badge tone={STATUS_TONE[status] ?? 'neutral'}>{status}</Badge>;
}

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: { id: T; label: string }[];
  value: T;
  onChange: (id: T) => void;
}) {
  return (
    <div role="tablist" className="flex gap-1 overflow-x-auto overflow-y-hidden border-b">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={value === t.id}
          onClick={() => onChange(t.id)}
          className={cn(
            '-mb-px cursor-pointer border-b-2 px-3 py-2.5 font-mono text-xs tracking-wider whitespace-nowrap uppercase transition-colors',
            value === t.id
              ? 'border-primary text-foreground'
              : 'text-muted-foreground hover:text-foreground border-transparent'
          )}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

/** Mono eyebrow, large tight title, hairline rule. `actions` sit on the right. */
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
    <header className="flex flex-wrap items-end justify-between gap-4 border-b pb-6">
      <div className="flex min-w-0 flex-col gap-2">
        {eyebrow && (
          <p className="text-muted-foreground flex items-center gap-2 font-mono text-[11px] tracking-widest uppercase">
            <span className="bg-primary inline-block size-1.5 rounded-full" />
            {eyebrow}
          </p>
        )}
        <h1 className="text-brand w-fit text-4xl leading-tight font-semibold tracking-[-0.045em] sm:text-5xl">
          {title}
        </h1>
        {subtitle && <p className="text-muted-foreground text-sm">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-3">{actions}</div>}
    </header>
  );
}

export function ErrorText({ children }: { children: React.ReactNode }) {
  if (!children) return null;
  return (
    <p role="alert" className="text-destructive text-sm">
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
