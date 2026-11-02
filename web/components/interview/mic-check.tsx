'use client';

import { useEffect, useRef, useState } from 'react';
import { MicrophoneIcon } from '@phosphor-icons/react';

/** Asks for microphone access and shows a live input level, so problems surface before joining.
 *  Calls onReady(true) once audio is actually coming in. */
export function MicCheck({ onReady }: { onReady: (ok: boolean) => void }) {
  const [level, setLevel] = useState(0);
  const [error, setError] = useState('');
  const heard = useRef(false);

  useEffect(() => {
    let stream: MediaStream | undefined;
    let ctx: AudioContext | undefined;
    let raf = 0;
    navigator.mediaDevices
      ?.getUserMedia({ audio: true })
      .then((s) => {
        stream = s;
        ctx = new AudioContext();
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 512;
        ctx.createMediaStreamSource(s).connect(analyser);
        const buf = new Float32Array(analyser.fftSize);
        const tick = () => {
          analyser.getFloatTimeDomainData(buf);
          const rms = Math.sqrt(buf.reduce((a, v) => a + v * v, 0) / buf.length);
          const l = Math.min(1, rms * 8);
          setLevel(l);
          if (l > 0.08 && !heard.current) {
            heard.current = true;
            onReady(true);
          }
          raf = requestAnimationFrame(tick);
        };
        tick();
      })
      .catch(() => {
        setError('Microphone access was blocked. Allow it in your browser settings, then reload.');
        onReady(false);
      });
    if (!navigator.mediaDevices)
      setError('This browser can’t access a microphone. Try Chrome, Edge, Firefox, or Safari.');
    return () => {
      cancelAnimationFrame(raf);
      stream?.getTracks().forEach((t) => t.stop()); // release the mic before LiveKit opens it
      ctx?.close();
    };
  }, [onReady]);

  return (
    <div className="flex w-full flex-col gap-2">
      <span className="flex items-center gap-2 text-sm font-medium">
        <MicrophoneIcon /> Microphone check
      </span>
      {error ? (
        <p role="alert" className="text-destructive text-sm">
          {error}
        </p>
      ) : (
        <>
          <div
            className="bg-muted h-2 overflow-hidden rounded-full"
            role="meter"
            aria-label="Microphone level"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(level * 100)}
          >
            <div
              className="bg-primary h-full transition-[width] duration-75"
              style={{ width: `${level * 100}%` }}
            />
          </div>
          <span className="text-muted-foreground text-xs">
            {heard.current ? 'We can hear you.' : 'Say something to test your microphone.'}
          </span>
        </>
      )}
    </div>
  );
}
