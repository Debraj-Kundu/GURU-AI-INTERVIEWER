'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowClockwiseIcon, FileArrowUpIcon, LinkIcon, TrashIcon } from '@phosphor-icons/react';
import { Card, ErrorText, Input, StatusBadge, usePoll } from '@/components/guru/ui';
import { Button } from '@/components/ui/button';
import { type Source, api } from '@/lib/api';
import { cn } from '@/lib/shadcn/utils';

const ACCEPT = '.pdf,.docx,.txt,.md,.csv';
const MAX_BYTES = 20 * 1024 * 1024; // mirrors backend MAX_FILE_BYTES
const BUSY = new Set(['uploading', 'queued', 'parsing', 'parsed', 'embedding']);

function size(bytes: number | null) {
  if (bytes == null) return '';
  return bytes > 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(1)} MB`
    : `${Math.ceil(bytes / 1024)} KB`;
}

/** The company's knowledge base, shared by all of its roles. */
export function KnowledgeBasePanel({ companyId }: { companyId: string }) {
  const [sources, setSources] = useState<Source[]>([]);
  const [errors, setErrors] = useState<string[]>([]);
  const [url, setUrl] = useState('');
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(0);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    api.listSources(companyId).then(setSources, (e) => setErrors([e.message]));
  }, [companyId]);
  useEffect(load, [load]);
  usePoll(load, uploading > 0 || sources.some((s) => BUSY.has(s.status)));

  async function upload(files: File[]) {
    const problems: string[] = [];
    const ok = files.filter((f) => {
      const ext = f.name.slice(f.name.lastIndexOf('.')).toLowerCase();
      if (!ACCEPT.split(',').includes(ext))
        problems.push(`${f.name}: unsupported type (${ACCEPT}).`);
      else if (f.size > MAX_BYTES) problems.push(`${f.name}: larger than 20 MB.`);
      else return true;
      return false;
    });
    setErrors(problems);
    setUploading((n) => n + ok.length);
    await Promise.all(
      ok.map((f) =>
        api
          .uploadFile(companyId, f)
          .catch((e) => setErrors((prev) => [...prev, `${f.name}: ${e.message}`]))
          .finally(() => {
            setUploading((n) => n - 1);
            load();
          })
      )
    );
  }

  async function addUrl() {
    if (!/^https?:\/\//.test(url)) return setErrors(['Links must start with http:// or https://']);
    try {
      await api.addUrl(companyId, url.trim());
      setUrl('');
      setErrors([]);
      load();
    } catch (e) {
      setErrors([(e as Error).message]);
    }
  }

  const act = (fn: () => Promise<unknown>) => fn().then(load, (e) => setErrors([e.message]));
  const ready = sources.filter((s) => s.status === 'ready');

  return (
    <div className="flex flex-col gap-4">
      <p className="text-muted-foreground text-sm">
        Shared by every role at this company. The interviewer answers candidate questions about the
        company only from these sources. Files up to 20 MB, at most 50 sources per company. Scanned
        PDFs without a text layer can&apos;t be read.
      </p>

      <div className="grid gap-4 md:grid-cols-2">
        <div
          role="button"
          tabIndex={0}
          onClick={() => fileRef.current?.click()}
          onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && fileRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            upload(Array.from(e.dataTransfer.files));
          }}
          className={cn(
            'flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed p-6 text-center transition-colors',
            dragging ? 'border-primary bg-accent' : 'hover:border-ring'
          )}
        >
          <FileArrowUpIcon className="size-8" />
          <span className="text-sm font-medium">
            {uploading
              ? `Uploading ${uploading} file${uploading > 1 ? 's' : ''}…`
              : 'Drop files or click to upload'}
          </span>
          <span className="text-muted-foreground text-xs">PDF, DOCX, TXT, MD, CSV</span>
          <input
            ref={fileRef}
            type="file"
            multiple
            accept={ACCEPT}
            className="hidden"
            onChange={(e) => {
              upload(Array.from(e.target.files ?? []));
              e.target.value = '';
            }}
          />
        </div>

        <Card className="flex flex-col justify-center gap-2">
          <span className="flex items-center gap-2 text-sm font-medium">
            <LinkIcon /> Add a web page
          </span>
          <div className="flex gap-2">
            <Input
              type="url"
              placeholder="https://company.com/about"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && addUrl()}
            />
            <Button onClick={addUrl} disabled={!url}>
              Add
            </Button>
          </div>
          <span className="text-muted-foreground text-xs">
            Public pages only. The page&apos;s robots.txt is respected.
          </span>
        </Card>
      </div>

      {errors.map((e, i) => (
        <ErrorText key={i}>{e}</ErrorText>
      ))}

      <Card className="p-0">
        <div className="text-muted-foreground flex items-center justify-between border-b px-5 py-3 text-xs">
          <span>
            {sources.length} source{sources.length === 1 ? '' : 's'} · {ready.length} ready ·{' '}
            {ready.reduce((n, s) => n + s.chunks, 0)} chunks
          </span>
        </div>
        {sources.length === 0 && (
          <p className="text-muted-foreground p-5 text-center text-sm">No sources yet.</p>
        )}
        <ul className="divide-y">
          {sources.map((s) => (
            <li key={s.id} className="flex flex-wrap items-center gap-3 px-5 py-3">
              <span className="bg-muted rounded px-1.5 py-0.5 font-mono text-[10px] uppercase">
                {s.kind}
              </span>
              <span className="min-w-0 flex-1 truncate text-sm" title={s.name}>
                {s.name}
              </span>
              <span className="text-muted-foreground text-xs">
                {s.status === 'ready' ? `${s.chunks} chunks` : size(s.size_bytes)}
              </span>
              <StatusBadge status={s.status} />
              {(s.status === 'failed' || s.status === 'duplicate') && (
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label="Retry"
                  onClick={() => act(() => api.retrySource(s.id))}
                >
                  <ArrowClockwiseIcon />
                </Button>
              )}
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={`Delete ${s.name}`}
                disabled={BUSY.has(s.status)}
                onClick={() => confirm(`Delete ${s.name}?`) && act(() => api.deleteSource(s.id))}
              >
                <TrashIcon />
              </Button>
              {s.error && <p className="text-destructive w-full text-xs">{s.error}</p>}
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
