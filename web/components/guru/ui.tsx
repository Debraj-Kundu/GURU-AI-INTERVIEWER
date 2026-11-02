// Small form/layout primitives in the starter's shadcn style (theme tokens from styles/globals.css).
import * as React from 'react';
import { cn } from '@/lib/shadcn/utils';

const field =
  'border-input bg-background placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 w-full rounded-md border px-3 py-2 text-sm shadow-xs outline-none focus-visible:ring-[3px] disabled:opacity-50';

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
      <span className="text-sm font-medium">{label}</span>
      {children}
      {hint && <span className="text-muted-foreground text-xs">{hint}</span>}
    </label>
  );
}

export function Card({ className, ...props }: React.ComponentProps<'section'>) {
  return (
    <section className={cn('bg-card rounded-xl border p-5 shadow-xs', className)} {...props} />
  );
}

const tones = {
  neutral: 'bg-muted text-muted-foreground',
  busy: 'bg-blue-500/15 text-blue-700 dark:text-blue-300',
  good: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300',
  warn: 'bg-amber-500/15 text-amber-700 dark:text-amber-300',
  bad: 'bg-red-500/15 text-red-700 dark:text-red-300',
};

export type Tone = keyof typeof tones;

export function Badge({ tone = 'neutral', children }: { tone?: Tone; children: React.ReactNode }) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium',
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
  declined: 'warn',
  duplicate: 'warn',
  stale: 'warn',
  uploading: 'busy',
  queued: 'busy',
  parsing: 'busy',
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
    <div role="tablist" className="flex gap-1 overflow-x-auto border-b">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={value === t.id}
          onClick={() => onChange(t.id)}
          className={cn(
            '-mb-px border-b-2 px-3 py-2 text-sm font-medium whitespace-nowrap transition-colors',
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
