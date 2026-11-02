'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { useTheme } from 'next-themes';
import { TokenSource } from 'livekit-client';
import { useSession, useSessionContext } from '@livekit/components-react';
import { WarningIcon } from '@phosphor-icons/react';
import { AgentSessionProvider } from '@/components/agents-ui/agent-session-provider';
import { AgentSessionView_01 } from '@/components/agents-ui/blocks/agent-session-view-01';
import { StartAudioButton } from '@/components/agents-ui/start-audio-button';
import { MicCheck } from '@/components/interview/mic-check';
import { Button } from '@/components/ui/button';
import { Toaster } from '@/components/ui/sonner';
import { useAgentErrors } from '@/hooks/useAgentErrors';
import { type InterviewInfo, api } from '@/lib/api';

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
  const tokenSource = useMemo(
    () =>
      TokenSource.custom(async () => {
        const t = await api.interviewToken(id);
        return { serverUrl: t.server_url, participantToken: t.participant_token };
      }),
    [id]
  );
  const session = useSession(tokenSource, { agentName: AGENT_NAME });

  return (
    <AgentSessionProvider session={session}>
      <main className="grid min-h-svh place-items-center p-4">
        {error && <Message title="Can’t open this interview" body={error} />}
        {!error && !info && <p className="text-muted-foreground text-sm">Loading…</p>}
        {info && <Flow info={info} />}
      </main>
      <StartAudioButton label="Start audio" />
      <Toaster icons={{ warning: <WarningIcon weight="bold" /> }} position="top-center" />
    </AgentSessionProvider>
  );
}

function Message({ title, body }: { title: string; body: string }) {
  return (
    <section className="flex max-w-md flex-col items-center gap-2 text-center">
      <h1 className="text-xl font-semibold">{title}</h1>
      <p className="text-muted-foreground">{body}</p>
    </section>
  );
}

function Flow({ info }: { info: InterviewInfo }) {
  const { isConnected, start } = useSessionContext();
  const { resolvedTheme } = useTheme();
  const [checked, setChecked] = useState<boolean[]>(CONSENTS.map(() => false));
  const [micOk, setMicOk] = useState(false);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState('');
  const [ended, setEnded] = useState(info.status === 'completed' || info.status === 'declined');
  const wasConnected = useRef(false);
  useAgentErrors();

  // The agent ends the session (goodbye, time cap, silence, declined consent) by closing the room.
  useEffect(() => {
    if (isConnected) wasConnected.current = true;
    else if (wasConnected.current) setEnded(true);
  }, [isConnected]);

  if (ended)
    return (
      <Message
        title="Thank you"
        body="Your interview is complete. The recruiter will be in touch about next steps. You can close this tab."
      />
    );

  if (isConnected)
    return (
      <>
        <Countdown seconds={info.max_duration_sec} />
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
    <section className="flex w-full max-w-lg flex-col gap-6 rounded-2xl border p-6 shadow-sm">
      <div>
        <p className="text-muted-foreground text-sm">{info.company_name}</p>
        <h1 className="text-2xl font-semibold">{info.job_title}</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          {info.candidate_name ? `Hi ${info.candidate_name}. ` : ''}A voice interview of about{' '}
          {Math.round(info.max_duration_sec / 60)} minutes. Find a quiet place and use headphones if
          you can.
        </p>
      </div>

      <fieldset className="flex flex-col gap-3">
        <legend className="mb-2 text-sm font-medium">Before you start</legend>
        {CONSENTS.map((c, i) => (
          <label key={i} className="flex items-start gap-3 text-sm">
            <input
              type="checkbox"
              className="mt-0.5 size-4"
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
      <Button
        size="lg"
        disabled={!ready || starting}
        onClick={async () => {
          setStarting(true);
          setStartError('');
          try {
            await start();
          } catch (e) {
            setStartError((e as Error).message || 'Couldn’t start the interview.');
            setStarting(false);
          }
        }}
      >
        {starting ? 'Connecting…' : 'Start interview'}
      </Button>
      <p className="text-muted-foreground text-xs">
        The interviewer will also ask for your consent out loud. You can end the interview at any
        time.
      </p>
    </section>
  );
}

/** Time left before the interview's hard cap (the agent enforces it; this is just a display). */
function Countdown({ seconds }: { seconds: number }) {
  const [left, setLeft] = useState(seconds);
  useEffect(() => {
    const startedAt = Date.now();
    const t = setInterval(
      () => setLeft(Math.max(0, seconds - Math.floor((Date.now() - startedAt) / 1000))),
      1000
    );
    return () => clearInterval(t);
  }, [seconds]);
  return (
    <div
      aria-label="Time remaining"
      className="bg-background/80 fixed top-4 left-1/2 z-[60] -translate-x-1/2 rounded-full border px-3 py-1 font-mono text-sm backdrop-blur"
    >
      {Math.floor(left / 60)}:{String(left % 60).padStart(2, '0')}
    </div>
  );
}
