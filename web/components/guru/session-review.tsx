'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowClockwiseIcon } from '@phosphor-icons/react';
import { Badge, Card, ErrorText, StatusBadge, formatDuration, usePoll } from '@/components/guru/ui';
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

  if (!s) return <ErrorText>{error}</ErrorText>;

  const criteria = new Map(s.criteria.map((c) => [c.id, c]));
  const jump = (turn: number) => {
    setHighlight(turn);
    document
      .getElementById(`turn-${turn}`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <Link
            href={`/jobs/${s.job_id}`}
            className="text-muted-foreground text-sm hover:underline"
          >
            ← Job
          </Link>
          <h1 className="text-2xl font-semibold">{s.candidate_name || 'Unnamed candidate'}</h1>
          <p className="text-muted-foreground text-sm">
            {s.started_at && new Date(s.started_at).toLocaleString()} ·{' '}
            {formatDuration(s.duration_sec)}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <StatusBadge status={s.score_status} />
          {s.score?.overall != null && (
            <span className="font-mono text-3xl font-semibold">
              {s.score.overall.toFixed(2)}
              <span className="text-muted-foreground text-base"> / 5</span>
            </span>
          )}
          <Button
            variant="outline"
            onClick={() => api.rescore(id).then(load, (e) => setError(e.message))}
            disabled={s.score_status === 'scoring' || s.status !== 'completed'}
          >
            <ArrowClockwiseIcon /> Re-score
          </Button>
        </div>
      </div>
      <ErrorText>{error || s.score_error}</ErrorText>
      {s.score && (
        <p className="rounded-md bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-300">
          {s.score.advisory}
        </p>
      )}

      <div className="grid gap-5 lg:grid-cols-[1fr_1.1fr]">
        <div className="flex flex-col gap-3">
          <h2 className="font-medium">Scores</h2>
          {s.score_status === 'scoring' && (
            <Card className="text-muted-foreground text-sm">Scoring…</Card>
          )}
          {s.score?.criteria.map((c) => {
            const meta = criteria.get(c.criterion_id);
            return (
              <Card key={c.criterion_id} className="flex flex-col gap-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{meta?.name ?? c.criterion_id}</span>
                  {c.insufficient_evidence ? (
                    <Badge tone="warn">insufficient evidence</Badge>
                  ) : (
                    <span className="font-mono text-lg font-semibold">{c.score} / 5</span>
                  )}
                </div>
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
                        className="bg-muted hover:bg-accent rounded px-1.5 py-0.5 font-mono"
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
              <h2 className="mt-2 font-medium">Observer notes</h2>
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
                  {n.note.signals_hit.length > 0 && <p>✓ {n.note.signals_hit.join('; ')}</p>}
                  {n.note.signals_missing.length > 0 && (
                    <p className="text-muted-foreground">✗ {n.note.signals_missing.join('; ')}</p>
                  )}
                  {n.note.red_flags.length > 0 && (
                    <p className="text-destructive">⚑ {n.note.red_flags.join('; ')}</p>
                  )}
                </Card>
              ))}
            </>
          )}
        </div>

        <div className="flex flex-col gap-3">
          <h2 className="font-medium">Transcript</h2>
          <Card className="flex max-h-[75vh] flex-col gap-3 overflow-y-auto">
            {s.transcript.length === 0 && (
              <p className="text-muted-foreground text-sm">No transcript.</p>
            )}
            {s.transcript.map((t) => (
              <div
                key={t.idx}
                id={`turn-${t.idx}`}
                className={cn(
                  'rounded-lg px-3 py-2 text-sm transition-colors',
                  t.speaker === 'candidate' ? 'bg-muted ml-8' : 'mr-8',
                  highlight === t.idx && 'ring-primary ring-2'
                )}
              >
                <span className="text-muted-foreground font-mono text-[10px] uppercase">
                  {t.idx} · {t.speaker}
                </span>
                <p>{t.text}</p>
              </div>
            ))}
          </Card>
        </div>
      </div>
    </div>
  );
}
