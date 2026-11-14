'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import {
  ArrowClockwiseIcon,
  CheckIcon,
  ClockIcon,
  CopyIcon,
  EyeIcon,
  LinkIcon,
} from '@phosphor-icons/react';
import {
  Avatar,
  Card,
  Count,
  EmptyState,
  ErrorText,
  Input,
  ScoreRing,
  StatusBadge,
  Tabs,
  formatDuration,
} from '@/components/guru/ui';
import { Button } from '@/components/ui/button';
import { API_URL, type SessionSummary, api } from '@/lib/api';

export function InterviewsPanel({ jobId, canCreate }: { jobId: string; canCreate: boolean }) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [name, setName] = useState('');
  const [ttlMin, setTtlMin] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState('');
  const [view, setView] = useState<'upcoming' | 'completed'>('upcoming');

  const load = useCallback(() => {
    api.listSessions(jobId).then(setSessions, (e) => setError(e.message));
  }, [jobId]);
  useEffect(load, [load]);
  useEffect(() => {
    api.inviteDefaults().then(
      (d) => setTtlMin(d.ttl_min),
      () => {}
    );
  }, []);
  // Interviews start, end and get scored in the background. The API pushes each change over
  // server-sent events; reload on every event and on every (re)connect, so a change made while
  // the stream was down is still picked up.
  useEffect(() => {
    const events = new EventSource(`${API_URL}/jobs/${jobId}/sessions/events`);
    events.onopen = load;
    events.onmessage = load;
    return () => events.close();
  }, [jobId, load]);

  const isUpcoming = (s: SessionSummary) =>
    (s.status === 'created' && !s.expired) || s.status === 'active';
  const upcoming = sessions.filter(isUpcoming);
  const shown = view === 'upcoming' ? upcoming : sessions.filter((s) => !isUpcoming(s));

  const link = (id: string) => `${window.location.origin}/interview/${id}`;

  async function create() {
    setError('');
    try {
      const s = await api.createSession(jobId, name.trim());
      setName('');
      await navigator.clipboard?.writeText(link(s.id)).then(
        () => setCopied(s.id),
        () => {}
      );
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function renew(id: string) {
    setError('');
    try {
      await api.renewLink(id);
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <Card className="relative flex flex-col gap-3 overflow-hidden p-4">
        <svg
          aria-hidden
          viewBox="0 0 200 60"
          className="text-primary/10 pointer-events-none absolute -top-2 -right-6 h-20 w-56"
        >
          {Array.from({ length: 24 }, (_, i) => {
            const h = 8 + Math.abs(Math.sin(i * 0.9)) * 40;
            return (
              <rect
                key={i}
                x={i * 8}
                y={30 - h / 2}
                width="4"
                height={h}
                rx="2"
                fill="currentColor"
              />
            );
          })}
        </svg>
        <div className="relative">
          <p className="font-medium">Invite a candidate</p>
          <p className="text-muted-foreground text-xs">
            Share a private link to a voice interview.
          </p>
        </div>
        <div className="relative flex flex-col gap-2">
          <Input
            placeholder="Candidate name (optional)"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Button onClick={create} disabled={!canCreate}>
            <LinkIcon /> Generate link
          </Button>
        </div>
        <span className="text-muted-foreground text-xs">
          {canCreate
            ? `The link is copied for you to share${ttlMin ? `; it expires after ${ttlMin} minutes unless the interview has started` : ''}. Each link uses a snapshot of the current plan, so later edits don’t change it.`
            : 'Generate an up-to-date plan first.'}
        </span>
        <ErrorText>{error}</ErrorText>
      </Card>

      <Tabs
        className="w-full"
        tabs={[
          {
            id: 'upcoming',
            label: (
              <>
                Upcoming <Count n={upcoming.length} />
              </>
            ),
          },
          {
            id: 'completed',
            label: (
              <>
                Completed <Count n={sessions.length - upcoming.length} />
              </>
            ),
          },
        ]}
        value={view}
        onChange={setView}
      />
      {shown.length === 0 && (
        <EmptyState
          className="py-8"
          title={view === 'upcoming' ? 'No upcoming interviews' : 'No completed interviews'}
          body={view === 'upcoming' ? 'Generate a link above to invite someone.' : undefined}
        />
      )}
      <ul className="flex flex-col gap-2">
        {shown.map((s, i) => (
          <li key={s.id} className="animate-rise" style={{ animationDelay: `${i * 40}ms` }}>
            <Card className="hover:border-primary/30 flex flex-wrap items-center gap-3 p-4 text-sm transition-colors">
              <div className="flex min-w-0 flex-1 items-center gap-3">
                <Avatar name={s.candidate_name || '?'} />
                <div className="min-w-0">
                  <p className="truncate font-medium">{s.candidate_name || 'Unnamed candidate'}</p>
                  <p className="text-muted-foreground flex items-center gap-1 text-xs">
                    {new Date(s.created_at).toLocaleDateString(undefined, {
                      month: 'short',
                      day: 'numeric',
                      hour: 'numeric',
                      minute: '2-digit',
                    })}
                    <span>·</span>
                    <ClockIcon /> {formatDuration(s.duration_sec)}
                  </p>
                </div>
              </div>
              {s.status === 'completed' && <ScoreRing value={s.overall} size={44} />}
              <div className="flex w-full flex-wrap items-center gap-1.5">
                <StatusBadge status={s.expired ? 'expired' : s.status} />
                {s.status === 'completed' && <StatusBadge status={s.score_status} />}
                <span className="flex-1" />
                {s.expired && (
                  <Button variant="outline" size="sm" onClick={() => renew(s.id)}>
                    <ArrowClockwiseIcon /> Renew link
                  </Button>
                )}
                {((s.status === 'created' && !s.expired) || s.status === 'active') && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() =>
                      navigator.clipboard.writeText(link(s.id)).then(() => setCopied(s.id))
                    }
                  >
                    {copied === s.id ? <CheckIcon className="text-emerald-500" /> : <CopyIcon />}{' '}
                    {copied === s.id ? 'Copied' : 'Copy link'}
                  </Button>
                )}
                {s.status === 'completed' && (
                  <Button asChild variant="outline" size="sm">
                    <Link href={`/sessions/${s.id}`}>
                      <EyeIcon /> Review
                    </Link>
                  </Button>
                )}
              </div>
            </Card>
          </li>
        ))}
      </ul>
    </div>
  );
}
