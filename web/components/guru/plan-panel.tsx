'use client';

import { useCallback, useEffect, useState } from 'react';
import { SparkleIcon } from '@phosphor-icons/react';
import { Badge, Card, ErrorText, StatusBadge, formatDuration, usePoll } from '@/components/guru/ui';
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
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
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
          <SparkleIcon /> {plan ? 'Regenerate plan' : 'Generate plan'}
        </Button>
      </div>
      {dirty && <p className="text-sm text-amber-600">Save your job changes first.</p>}
      <ErrorText>{error || (state?.status === 'failed' && state.error)}</ErrorText>

      {plan && (
        <>
          <Card className="flex flex-col gap-2">
            <h3 className="text-sm font-medium">Allowed tech stack</h3>
            <p className="text-muted-foreground text-xs">
              The interviewer only discusses these tools when candidates ask about the company.
            </p>
            <div className="flex flex-wrap gap-1.5">
              {plan.allowed_tech_stack.map((t) => (
                <Badge key={t}>{t}</Badge>
              ))}
            </div>
          </Card>

          <h3 className="font-medium">
            {plan.questions.length} questions · {formatDuration(total)} planned
          </h3>
          {plan.questions.map((q, i) => (
            <Card key={i} className="flex flex-col gap-2">
              <div className="flex items-start justify-between gap-3">
                <p className="font-medium">
                  {i + 1}. {q.text}
                </p>
                <Badge>{formatDuration(q.time_budget_sec)}</Badge>
              </div>
              <div className="grid gap-3 text-sm md:grid-cols-2">
                <div>
                  <span className="text-muted-foreground text-xs">Expected signals</span>
                  <ul className="list-disc pl-5">
                    {q.expected_signals.map((s) => (
                      <li key={s}>{s}</li>
                    ))}
                  </ul>
                </div>
                <div>
                  <span className="text-muted-foreground text-xs">Follow-up probes</span>
                  <ul className="list-disc pl-5">
                    {q.follow_up_probes.map((s) => (
                      <li key={s}>{s}</li>
                    ))}
                  </ul>
                </div>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {q.linked_criteria.map((c) => (
                  <Badge key={c} tone="busy">
                    {c}
                  </Badge>
                ))}
              </div>
            </Card>
          ))}

          {plan.skipped_questions.length > 0 && (
            <Card className="flex flex-col gap-2">
              <h3 className="text-sm font-medium">Skipped from the bank</h3>
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
