'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  CheckCircleIcon,
  CircleNotchIcon,
  ClockIcon,
  QuestionIcon,
  SparkleIcon,
} from '@phosphor-icons/react';
import {
  Badge,
  Card,
  EmptyState,
  ErrorText,
  SectionTitle,
  Skeleton,
  StatusBadge,
  formatDuration,
  usePoll,
} from '@/components/guru/ui';
import { Button } from '@/components/ui/button';
import { type PlanState, api } from '@/lib/api';

export function PlanPanel({
  jobId,
  dirty,
  onPlanChange,
}: {
  jobId: string;
  dirty: boolean;
  onPlanChange?: (p: PlanState) => void;
}) {
  const [state, setState] = useState<PlanState | null>(null);
  const [error, setError] = useState('');

  const load = useCallback(() => {
    api.getPlan(jobId).then(
      (p) => {
        setState(p);
        onPlanChange?.(p);
      },
      (e) => setError(e.message)
    );
  }, [jobId, onPlanChange]);
  useEffect(load, [load]);
  usePoll(load, state?.status === 'generating');

  async function generate() {
    setError('');
    try {
      setState(await api.generatePlan(jobId));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const plan = state?.plan;
  const total = plan?.questions.reduce((s, q) => s + q.time_budget_sec, 0) ?? 0;

  return (
    <div className="flex flex-col gap-4">
      <Card className="flex flex-wrap items-center justify-between gap-3 p-4">
        <div className="flex items-center gap-3">
          <StatusBadge status={state?.stale ? 'stale' : (state?.status ?? 'none')} />
          <span className="text-muted-foreground text-sm">
            {state?.status === 'generating'
              ? 'Planning… this usually takes under a minute.'
              : state?.stale
                ? 'The job changed since this plan was made. Regenerate before creating interview links.'
                : 'The planner picks and orders questions to fit the interview length.'}
          </span>
        </div>
        <Button onClick={generate} disabled={dirty || state?.status === 'generating'}>
          {state?.status === 'generating' ? (
            <CircleNotchIcon className="animate-spin" />
          ) : (
            <SparkleIcon weight="fill" />
          )}{' '}
          {plan ? 'Regenerate plan' : 'Generate plan'}
        </Button>
      </Card>
      {dirty && (
        <p className="rounded-lg border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-300">
          Save your job changes first.
        </p>
      )}
      {state?.status === 'generating' && (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-24" />
          <Skeleton className="h-40" />
          <Skeleton className="h-40" />
        </div>
      )}
      {state && !plan && state.status !== 'generating' && (
        <EmptyState
          title="No plan yet"
          body="Generate a plan and the AI planner will pick and time questions for this role."
        />
      )}
      <ErrorText>{error || (state?.status === 'failed' && state.error)}</ErrorText>

      {plan && (
        <>
          <Card className="flex flex-col gap-2">
            <SectionTitle>Allowed tech stack</SectionTitle>
            <p className="text-muted-foreground text-xs">
              The interviewer only discusses these tools when candidates ask about the company.
            </p>
            <div className="flex flex-wrap gap-1.5">
              {plan.allowed_tech_stack.map((t) => (
                <Badge key={t} tone="brand">
                  {t}
                </Badge>
              ))}
            </div>
          </Card>

          {state?.criteria?.some((c) => c.source && c.source !== 'user') && (
            <Card className="flex flex-col gap-3">
              <SectionTitle>Scoring criteria (generated)</SectionTitle>
              <p className="text-muted-foreground text-xs">
                No criteria were entered for this role, so these were generated with the plan.
                Regenerating the plan generates them again; add your own in the role to keep them
                fixed.
              </p>
              <ul className="flex flex-col gap-2 text-sm">
                {state.criteria.map((c) => (
                  <li key={c.id} className="flex flex-col gap-0.5">
                    <span className="flex flex-wrap items-center gap-2 font-medium">
                      {c.name}
                      <span className="text-muted-foreground text-xs">weight {c.weight}</span>
                      <Badge tone={c.source === 'inferred' ? 'warn' : 'brand'}>
                        {c.source === 'inferred' ? 'Inferred' : 'From the job description'}
                      </Badge>
                    </span>
                    <span className="text-muted-foreground">{c.description}</span>
                  </li>
                ))}
              </ul>
            </Card>
          )}

          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between text-sm">
              <span className="flex items-center gap-2 font-medium">
                <QuestionIcon className="text-primary" /> {plan.questions.length} questions
              </span>
              <span className="text-muted-foreground flex items-center gap-1.5 tabular-nums">
                <ClockIcon /> {formatDuration(total)} planned
              </span>
            </div>
            {/* Time split across questions. */}
            <div className="bg-muted flex h-2 gap-0.5 overflow-hidden rounded-full">
              {plan.questions.map((q, i) => (
                <div
                  key={i}
                  title={`Q${i + 1} · ${formatDuration(q.time_budget_sec)}`}
                  className="animate-in slide-in-from-left fill-mode-both h-full duration-700"
                  style={{
                    width: `${(q.time_budget_sec / (total || 1)) * 100}%`,
                    background: `var(--chart-${(i % 5) + 1})`,
                    animationDelay: `${i * 80}ms`,
                  }}
                />
              ))}
            </div>
          </div>
          {plan.questions.map((q, i) => (
            <Card
              key={i}
              className="animate-rise flex flex-col gap-3"
              style={{ animationDelay: `${i * 60}ms` }}
            >
              <div className="flex items-start gap-3">
                <span
                  className="grid size-7 shrink-0 place-items-center rounded-md text-xs font-semibold text-white"
                  style={{ background: `var(--chart-${(i % 5) + 1})` }}
                >
                  {i + 1}
                </span>
                <p className="flex-1 font-medium">{q.text}</p>
                <Badge>
                  <ClockIcon /> {formatDuration(q.time_budget_sec)}
                </Badge>
              </div>
              <div className="grid gap-3 text-sm md:grid-cols-2">
                <div>
                  <SectionTitle className="mb-2">Expected signals</SectionTitle>
                  <ul className="flex flex-col gap-1.5">
                    {q.expected_signals.map((s) => (
                      <li key={s} className="flex gap-2">
                        <CheckCircleIcon
                          className="mt-0.5 shrink-0 text-emerald-500"
                          weight="fill"
                        />
                        {s}
                      </li>
                    ))}
                  </ul>
                </div>
                <div>
                  <SectionTitle className="mb-2">Follow-up probes</SectionTitle>
                  <ul className="flex flex-col gap-1.5">
                    {q.follow_up_probes.map((s) => (
                      <li key={s} className="text-muted-foreground flex gap-2">
                        <span className="text-primary mt-px shrink-0">↳</span>
                        {s}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
              <div className="flex flex-wrap gap-1.5 border-t pt-3">
                {q.linked_criteria.map((c) => (
                  <Badge key={c} tone="brand">
                    {c}
                  </Badge>
                ))}
              </div>
            </Card>
          ))}

          {plan.skipped_questions.length > 0 && (
            <Card className="flex flex-col gap-2">
              <SectionTitle>Skipped from the bank</SectionTitle>
              <ul className="flex flex-col gap-1 text-sm">
                {plan.skipped_questions.map((q, i) => (
                  <li key={i}>
                    {q.text} <span className="text-muted-foreground">— {q.reason}</span>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
