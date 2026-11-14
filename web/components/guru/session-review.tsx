'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import {
  ArrowClockwiseIcon,
  ArrowLeftIcon,
  CheckIcon,
  FlagIcon,
  InfoIcon,
  RobotIcon,
  XIcon,
} from '@phosphor-icons/react';
import {
  Avatar,
  Badge,
  Card,
  ErrorText,
  PageHeader,
  ScoreBar,
  ScoreRing,
  SectionTitle,
  Skeleton,
  StatusBadge,
  formatDuration,
  usePoll,
} from '@/components/guru/ui';
import { Button } from '@/components/ui/button';
import { type SessionDetail, api } from '@/lib/api';
import { cn } from '@/lib/shadcn/utils';

export function SessionReview({ id }: { id: string }) {
  const [s, setS] = useState<SessionDetail | null>(null);
  const [error, setError] = useState('');
  const [highlight, setHighlight] = useState<number | null>(null);

  const load = useCallback(() => {
    api.getSession(id).then(setS, (e) => setError(e.message));
  }, [id]);
  useEffect(load, [load]);
  usePoll(load, s?.score_status === 'scoring');

  if (!s)
    return error ? (
      <ErrorText>{error}</ErrorText>
    ) : (
      <div className="grid gap-5 lg:grid-cols-2">
        <Skeleton className="h-28 lg:col-span-2" />
        <Skeleton className="h-96" />
        <Skeleton className="h-96" />
      </div>
    );

  const criteria = new Map(s.criteria.map((c) => [c.id, c]));
  const jump = (turn: number) => {
    setHighlight(turn);
    document
      .getElementById(`turn-${turn}`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow={
          <Link href={`/jobs/${s.job_id}`} className="group flex items-center gap-1.5">
            <ArrowLeftIcon className="transition-transform group-hover:-translate-x-0.5" /> Back to
            role
          </Link>
        }
        title={
          <span className="flex items-center gap-3">
            <Avatar name={s.candidate_name || '?'} className="size-11 text-sm" />
            {s.candidate_name || 'Unnamed candidate'}
          </span>
        }
        subtitle={
          <>
            {s.started_at && new Date(s.started_at).toLocaleString()} ·{' '}
            {formatDuration(s.duration_sec)}
          </>
        }
        actions={
          <>
            <StatusBadge status={s.score_status} />
            {s.score?.overall != null && (
              <span className="flex items-center gap-2">
                <ScoreRing value={s.score.overall} size={64} />
                <span className="text-muted-foreground text-xs leading-tight">
                  Overall
                  <br />
                  out of 5
                </span>
              </span>
            )}
            <Button
              variant="outline"
              onClick={() => api.rescore(id).then(load, (e) => setError(e.message))}
              disabled={s.score_status === 'scoring' || s.status !== 'completed'}
            >
              <ArrowClockwiseIcon className={cn(s.score_status === 'scoring' && 'animate-spin')} />{' '}
              Re-score
            </Button>
          </>
        }
      />
      <ErrorText>{error || s.score_error}</ErrorText>
      {s.score && (
        <p className="flex items-start gap-2 rounded-lg border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-300">
          <InfoIcon className="mt-0.5 shrink-0" weight="fill" />
          {s.score.advisory}
        </p>
      )}

      <div className="grid gap-5 lg:grid-cols-[1fr_1.1fr]">
        <div className="flex flex-col gap-3">
          <SectionTitle>Scores</SectionTitle>
          {s.score_status === 'scoring' &&
            [0, 1, 2].map((i) => <Skeleton key={i} className="h-28 rounded-xl" />)}
          {s.score?.criteria.map((c, i) => {
            const meta = criteria.get(c.criterion_id);
            return (
              <Card
                key={c.criterion_id}
                className="animate-rise flex flex-col gap-2"
                style={{ animationDelay: `${i * 60}ms` }}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{meta?.name ?? c.criterion_id}</span>
                  {c.insufficient_evidence ? (
                    <Badge tone="warn">insufficient evidence</Badge>
                  ) : (
                    <span className="text-lg font-semibold tabular-nums">
                      {c.score}
                      <span className="text-muted-foreground text-sm font-normal"> / 5</span>
                    </span>
                  )}
                </div>
                {c.score != null && <ScoreBar value={c.score} />}
                {c.score != null && meta?.anchors[String(c.score)] && (
                  <p className="text-muted-foreground text-xs">
                    Anchor: {meta.anchors[String(c.score)]}
                  </p>
                )}
                <p className="text-sm">{c.rationale}</p>
                {c.evidence_turns.length > 0 && (
                  <div className="flex flex-wrap items-center gap-1.5 text-xs">
                    <span className="text-muted-foreground">Evidence:</span>
                    {c.evidence_turns.map((t) => (
                      <button
                        key={t}
                        onClick={() => jump(t)}
                        className="bg-muted hover:bg-primary/10 hover:text-primary cursor-pointer rounded px-1.5 py-0.5 font-mono transition-colors"
                      >
                        turn {t}
                      </button>
                    ))}
                  </div>
                )}
              </Card>
            );
          })}

          {s.notes.length > 0 && (
            <>
              <SectionTitle className="mt-2">Observer notes</SectionTitle>
              {s.notes.map((n, i) => (
                <Card key={i} className="flex flex-col gap-1.5 text-sm">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">Q{n.question_idx + 1}</span>
                    <Badge
                      tone={
                        n.note.depth === 'deep'
                          ? 'good'
                          : n.note.depth === 'adequate'
                            ? 'busy'
                            : 'warn'
                      }
                    >
                      {n.note.depth}
                    </Badge>
                    <span className="text-muted-foreground text-xs">
                      coverage {Math.round(n.note.coverage * 100)}%
                    </span>
                  </div>
                  {n.note.signals_hit.length > 0 && (
                    <p className="flex gap-2">
                      <CheckIcon className="mt-0.5 shrink-0 text-emerald-500" weight="bold" />
                      {n.note.signals_hit.join('; ')}
                    </p>
                  )}
                  {n.note.signals_missing.length > 0 && (
                    <p className="text-muted-foreground flex gap-2">
                      <XIcon className="mt-0.5 shrink-0" weight="bold" />
                      {n.note.signals_missing.join('; ')}
                    </p>
                  )}
                  {n.note.red_flags.length > 0 && (
                    <p className="text-destructive flex gap-2">
                      <FlagIcon className="mt-0.5 shrink-0" weight="fill" />
                      {n.note.red_flags.join('; ')}
                    </p>
                  )}
                </Card>
              ))}
            </>
          )}
        </div>

        <div className="flex flex-col gap-3">
          <SectionTitle>Transcript</SectionTitle>
          <Card className="flex max-h-[75vh] flex-col gap-4 overflow-y-auto lg:sticky lg:top-22">
            {s.transcript.length === 0 && (
              <p className="text-muted-foreground text-sm">No transcript.</p>
            )}
            {s.transcript.map((t) => (
              <div
                key={t.idx}
                id={`turn-${t.idx}`}
                className={cn(
                  'flex gap-2.5',
                  t.speaker === 'candidate' ? 'flex-row-reverse pl-8' : 'pr-8'
                )}
              >
                {t.speaker === 'candidate' ? (
                  <Avatar name={s.candidate_name || '?'} className="size-7 text-[10px]" />
                ) : (
                  <span className="bg-brand grid size-7 shrink-0 place-items-center rounded-full text-white">
                    <RobotIcon size={14} weight="fill" />
                  </span>
                )}
                <div
                  className={cn(
                    'rounded-2xl px-3.5 py-2 text-sm transition-all',
                    t.speaker === 'candidate'
                      ? 'bg-primary/10 rounded-tr-sm'
                      : 'bg-muted rounded-tl-sm',
                    highlight === t.idx && 'ring-primary ring-offset-card ring-2 ring-offset-2'
                  )}
                >
                  <span className="text-muted-foreground font-mono text-[10px] uppercase">
                    #{t.idx} · {t.speaker}
                  </span>
                  <p>{t.text}</p>
                </div>
              </div>
            ))}
          </Card>
        </div>
      </div>
    </div>
  );
}
