'use client';

import { useEffect, useMemo, useState } from 'react';
import { useTheme } from 'next-themes';
import { DisconnectReason, RoomEvent, TokenSource } from 'livekit-client';
import { useAgent, useSession, useSessionContext } from '@livekit/components-react';
import { AgentSessionProvider } from '@/components/agents-ui/agent-session-provider';
import { AgentSessionView_01 } from '@/components/agents-ui/blocks/agent-session-view-01';
import { StartAudioButton } from '@/components/agents-ui/start-audio-button';
import { MicCheck } from '@/components/interview/mic-check';
import { Button } from '@/components/ui/button';
import { ApiError, type InterviewInfo, api } from '@/lib/api';

const AGENT_NAME = 'interviewer'; // matches the agent worker and backend dispatch

const CONSENTS = [
  'I understand this interview is conducted by an AI interviewer.',
  'I agree that the interview is recorded and transcribed for the recruiter’s review.',
  'I agree that my answers are stored and used to evaluate my application, and kept for up to 180 days.',
];

export function InterviewApp({ id }: { id: string }) {
  const [info, setInfo] = useState<InterviewInfo | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    api
      .interviewInfo(id)
      .then(setInfo, (e) =>
        setError(e.status === 404 ? 'This interview link isn’t valid.' : e.message)
      );
  }, [id]);

  // The token comes from our backend (never a dev token server); it carries the agent dispatch.
  const [startedAt, setStartedAt] = useState<string | null>(null);
  const tokenSource = useMemo(
    () =>
      TokenSource.custom(async () => {
        const t = await api.interviewToken(id);
        setStartedAt(t.started_at);
        return { serverUrl: t.server_url, participantToken: t.participant_token };
      }),
    [id]
  );
  const session = useSession(tokenSource, { agentName: AGENT_NAME });

  return (
    <AgentSessionProvider session={session}>
      <main className="bg-grid grid min-h-svh place-items-center p-4">
        {error && <Message title="Can’t open this interview" body={error} />}
        {!error && !info && <p className="text-muted-foreground text-sm">Loading…</p>}
        {info && <Flow info={info} startedAt={startedAt} />}
      </main>
      <StartAudioButton label="Start audio" />
    </AgentSessionProvider>
  );
}

function Message({
  title,
  body,
  children,
}: {
  title: string;
  body: string;
  children?: React.ReactNode;
}) {
  return (
    <section className="flex max-w-md flex-col items-center gap-2 text-center">
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      <p className="text-muted-foreground">{body}</p>
      {children}
    </section>
  );
}

/** done: the agent finished (it deletes the room). no-agent: no interviewer joined in time.
 *  dropped: the candidate's connection ended; they can rejoin while the agent waits (2 min). */
type Outcome = 'done' | 'no-agent' | 'dropped' | 'expired' | null;

function Flow({ info, startedAt }: { info: InterviewInfo; startedAt: string | null }) {
  const { isConnected, start, end, room } = useSessionContext();
  const agent = useAgent();
  const { resolvedTheme } = useTheme();
  const [checked, setChecked] = useState<boolean[]>(CONSENTS.map(() => false));
  const [micOk, setMicOk] = useState(false);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState('');
  const [outcome, setOutcome] = useState<Outcome>(
    info.status === 'completed' || info.status === 'declined'
      ? 'done'
      : info.expired
        ? 'expired'
        : null
  );

  // Why the room closed decides what the candidate sees. The first reason wins.
  useEffect(() => {
    const onDisconnected = (reason?: DisconnectReason) =>
      setOutcome((o) => o ?? (reason === DisconnectReason.ROOM_DELETED ? 'done' : 'dropped'));
    room.on(RoomEvent.Disconnected, onDisconnected);
    return () => {
      room.off(RoomEvent.Disconnected, onDisconnected);
    };
  }, [room]);

  // No interviewer joined in time (e.g. the agent server is down). The backend still counts
  // this as not started, so the candidate can try again.
  useEffect(() => {
    if (isConnected && agent.state === 'failed') {
      setOutcome('no-agent');
      end();
    }
  }, [isConnected, agent.state, end]);

  const join = async () => {
    setStarting(true);
    setStartError('');
    try {
      await start();
      setOutcome(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setOutcome('done');
      else if (e instanceof ApiError && e.status === 410) setOutcome('expired');
      else setStartError((e as Error).message || 'Couldn’t start the interview.');
    }
    setStarting(false);
  };

  if (outcome === 'done')
    return (
      <Message
        title="Thank you"
        body="Your interview is complete. The recruiter will be in touch about next steps. You can close this tab."
      />
    );

  if (outcome === 'expired')
    return (
      <Message
        title="This link has expired"
        body="Interview links are only valid for a short time. Ask the recruiter to send you a new one."
      />
    );

  if (outcome)
    return (
      <Message
        {...(outcome === 'no-agent'
          ? {
              title: 'We couldn’t start your interview',
              body: 'The interviewer isn’t available right now. Your interview hasn’t started, so nothing is lost. Please try again in a few minutes.',
            }
          : {
              title: 'You’ve been disconnected',
              body: 'Rejoin within 2 minutes to continue where you left off. After that, the interview ends and can’t be restarted.',
            })}
      >
        {startError && (
          <p role="alert" className="text-destructive text-sm">
            {startError}
          </p>
        )}
        <Button className="mt-2" disabled={starting} onClick={join}>
          {starting ? 'Connecting…' : outcome === 'no-agent' ? 'Try again' : 'Rejoin interview'}
        </Button>
      </Message>
    );

  if (isConnected)
    return (
      <>
        <Countdown seconds={info.max_duration_sec} startedAt={startedAt} />
        <AgentSessionView_01
          preConnectMessage="Connecting you to the interviewer…"
          supportsChatInput={false}
          supportsVideoInput={false}
          supportsScreenShare={false}
          isPreConnectBufferEnabled
          themeMode={resolvedTheme === 'dark' ? 'dark' : 'light'}
          className="fixed inset-0"
        />
      </>
    );

  const ready = checked.every(Boolean) && micOk;
  return (
    <section className="bg-card animate-in fade-in slide-in-from-bottom-2 flex w-full max-w-lg flex-col gap-6 rounded-2xl border p-7 shadow-[0_15px_40px_-10px_#9564dd33] duration-500 motion-reduce:animate-none">
      <div>
        <p className="text-muted-foreground flex items-center gap-2 font-mono text-[11px] tracking-widest uppercase">
          <span className="bg-primary inline-block size-1.5 rounded-full" />
          {info.company_name}
        </p>
        <h1 className="text-brand mt-2 w-fit text-3xl font-semibold tracking-[-0.04em]">
          {info.job_title}
        </h1>
        <p className="text-muted-foreground mt-1 text-sm">
          {info.candidate_name ? `Hi ${info.candidate_name}. ` : ''}A voice interview of about{' '}
          {Math.round(info.max_duration_sec / 60)} minutes. Find a quiet place and use headphones if
          you can.
        </p>
      </div>

      <fieldset className="flex flex-col gap-3">
        <legend className="text-muted-foreground mb-2 font-mono text-[11px] tracking-widest uppercase">
          Before you start
        </legend>
        {CONSENTS.map((c, i) => (
          <label key={i} className="flex items-start gap-3 text-sm">
            <input
              type="checkbox"
              className="accent-primary mt-0.5 size-4"
              checked={checked[i]}
              onChange={(e) => setChecked(checked.map((v, k) => (k === i ? e.target.checked : v)))}
            />
            {c}
          </label>
        ))}
      </fieldset>

      <MicCheck onReady={setMicOk} />

      {startError && (
        <p role="alert" className="text-destructive text-sm">
          {startError}
        </p>
      )}
      <Button size="lg" disabled={!ready || starting} onClick={join}>
        {starting ? 'Connecting…' : 'Start interview'}
      </Button>
      <p className="text-muted-foreground text-xs">
        The interviewer will also ask for your consent out loud. You can end the interview at any
        time.
      </p>
    </section>
  );
}

/** Time left before the interview's hard cap (the agent enforces it; this is just a display).
 *  On a rejoin the backend's started_at keeps the real time left. */
function Countdown({ seconds, startedAt }: { seconds: number; startedAt: string | null }) {
  const [left, setLeft] = useState(seconds);
  useEffect(() => {
    const from = startedAt ? Date.parse(startedAt) : Date.now();
    const tick = () => setLeft(Math.max(0, seconds - Math.floor((Date.now() - from) / 1000)));
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, [seconds, startedAt]);
  return (
    <div
      aria-label="Time remaining"
      className="bg-background/80 fixed top-4 left-1/2 z-[60] -translate-x-1/2 rounded-full border px-3 py-1 font-mono text-sm backdrop-blur"
    >
      {Math.floor(left / 60)}:{String(left % 60).padStart(2, '0')}
    </div>
  );
}
