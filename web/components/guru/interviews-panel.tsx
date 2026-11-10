'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowClockwiseIcon, CopyIcon, LinkIcon } from '@phosphor-icons/react';
import { Card, ErrorText, Input, StatusBadge, Tabs, formatDuration } from '@/components/guru/ui';
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
      <Card className="flex flex-col gap-2 p-4">
        <span className="text-muted-foreground font-mono text-[11px] tracking-widest uppercase">
          Invite a candidate
        </span>
        <div className="flex flex-col gap-2">
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
        tabs={[
          { id: 'upcoming', label: `Upcoming (${upcoming.length})` },
          { id: 'completed', label: `Completed (${sessions.length - upcoming.length})` },
        ]}
        value={view}
        onChange={setView}
      />
      {shown.length === 0 && (
        <p className="text-muted-foreground py-4 text-center text-sm">No interviews here.</p>
      )}
      <ul className="flex flex-col gap-2">
        {shown.map((s) => (
          <li key={s.id}>
            <Card className="flex flex-wrap items-center gap-2 p-4 text-sm">
              <span className="w-full font-medium">{s.candidate_name || 'Unnamed candidate'}</span>
              <span className="text-muted-foreground w-full font-mono text-[11px]">
                {new Date(s.created_at).toLocaleString()} · {formatDuration(s.duration_sec)}
              </span>
              <StatusBadge status={s.expired ? 'expired' : s.status} />
              {s.status === 'completed' && <StatusBadge status={s.score_status} />}
              {s.overall != null && (
                <span className="ml-auto font-mono text-sm">{s.overall.toFixed(2)} / 5</span>
              )}
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
                  <CopyIcon /> {copied === s.id ? 'Copied' : 'Copy link'}
                </Button>
              )}
              {s.status === 'completed' && (
                <Button asChild variant="outline" size="sm">
                  <Link href={`/sessions/${s.id}`}>Review</Link>
                </Button>
              )}
            </Card>
          </li>
        ))}
      </ul>
    </div>
  );
}
