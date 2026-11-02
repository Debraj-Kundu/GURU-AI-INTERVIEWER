'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { CopyIcon, PlusIcon } from '@phosphor-icons/react';
import { Card, ErrorText, Input, StatusBadge, formatDuration, usePoll } from '@/components/guru/ui';
import { Button } from '@/components/ui/button';
import { type SessionSummary, api } from '@/lib/api';

export function InterviewsPanel({ jobId, canCreate }: { jobId: string; canCreate: boolean }) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [name, setName] = useState('');
  const [error, setError] = useState('');
  const [copied, setCopied] = useState('');

  const load = useCallback(() => {
    api.listSessions(jobId).then(setSessions, (e) => setError(e.message));
  }, [jobId]);
  useEffect(load, [load]);
  // Live interviews end and get scored in the background; keep the list fresh while that happens.
  usePoll(
    load,
    sessions.some((s) => s.status === 'active' || s.score_status === 'scoring'),
    5000
  );

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

  return (
    <div className="flex flex-col gap-4">
      <Card className="flex flex-col gap-2">
        <span className="text-sm font-medium">Create an interview link</span>
        <div className="flex flex-wrap gap-2">
          <Input
            className="max-w-xs"
            placeholder="Candidate name (optional)"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Button onClick={create} disabled={!canCreate}>
            <PlusIcon /> Create link
          </Button>
        </div>
        <span className="text-muted-foreground text-xs">
          {canCreate
            ? 'Each link uses a snapshot of the current plan, so later edits don’t change it. Anyone with the link can join.'
            : 'Generate an up-to-date plan first.'}
        </span>
        <ErrorText>{error}</ErrorText>
      </Card>

      <Card className="p-0">
        {sessions.length === 0 && (
          <p className="text-muted-foreground p-5 text-center text-sm">No interviews yet.</p>
        )}
        <ul className="divide-y">
          {sessions.map((s) => (
            <li key={s.id} className="flex flex-wrap items-center gap-3 px-5 py-3 text-sm">
              <span className="min-w-32 flex-1 font-medium">
                {s.candidate_name || 'Unnamed candidate'}
              </span>
              <span className="text-muted-foreground text-xs">
                {new Date(s.created_at).toLocaleString()}
              </span>
              <span className="text-muted-foreground text-xs">
                {formatDuration(s.duration_sec)}
              </span>
              <StatusBadge status={s.status} />
              {s.status === 'completed' && <StatusBadge status={s.score_status} />}
              {s.overall != null && (
                <span className="font-mono text-sm">{s.overall.toFixed(2)} / 5</span>
              )}
              {(s.status === 'created' || s.status === 'active') && (
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
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
