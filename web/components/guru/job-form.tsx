'use client';

import { useRef, useState } from 'react';
import { PlusIcon, TrashIcon, UploadSimpleIcon } from '@phosphor-icons/react';
import { Card, ErrorText, Field, Input, Textarea } from '@/components/guru/ui';
import { Button } from '@/components/ui/button';
import { type Criterion, type JobInput, type QuestionBank, api } from '@/lib/api';

type SectionProps = { value: JobInput; onChange: (v: JobInput) => void };

export const EMPTY_JOB: JobInput = {
  title: '',
  jd_text: '',
  max_duration_sec: 600,
  questions: [],
  criteria: [],
};

/** Server-side rules (backend JobIn) checked before saving, so errors show next to the form. */
export function jobProblems(j: JobInput): string[] {
  const out: string[] = [];
  if (!j.title.trim()) out.push('Add a job title.');
  if (!j.jd_text.trim()) out.push('Paste the job description.');
  if (j.questions.some((q) => !q.text.trim())) out.push('Every question needs text.');
  if (j.criteria.some((c) => !c.name.trim())) out.push('Every criterion needs a name.');
  if (j.criteria.some((c) => !(c.weight > 0))) out.push('Criterion weights must be above 0.');
  if (new Set(j.criteria.map((c) => c.id)).size !== j.criteria.length)
    out.push('Two criteria have the same name.');
  return out;
}

export function DetailsSection({ value, onChange }: SectionProps) {
  return (
    <Card className="grid gap-4 md:grid-cols-2">
      <Field label="Job title">
        <Input
          value={value.title}
          onChange={(e) => onChange({ ...value, title: e.target.value })}
        />
      </Field>
      <Field
        label="Interview length (minutes)"
        hint="5 to 60. The last 90 seconds are for candidate questions."
      >
        <Input
          type="number"
          min={5}
          max={60}
          value={Math.round(value.max_duration_sec / 60)}
          onChange={(e) =>
            onChange({
              ...value,
              max_duration_sec: Math.min(60, Math.max(5, Number(e.target.value) || 5)) * 60,
            })
          }
        />
      </Field>
      <Field label="Job description" className="md:col-span-2">
        <Textarea
          className="min-h-56"
          value={value.jd_text}
          onChange={(e) => onChange({ ...value, jd_text: e.target.value })}
        />
      </Field>
    </Card>
  );
}

/** Trims text and drops blank signal lines left over from editing, before sending to the API. */
export function normalizeJob(j: JobInput): JobInput {
  return {
    ...j,
    questions: j.questions.map((q) => ({
      ...q,
      text: q.text.trim(),
      expected_signals: q.expected_signals.map((s) => s.trim()).filter(Boolean),
    })),
  };
}

export function QuestionsSection({ value, onChange }: SectionProps) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [error, setError] = useState('');
  const set = (i: number, q: Partial<QuestionBank>) =>
    onChange({
      ...value,
      questions: value.questions.map((x, k) => (k === i ? { ...x, ...q } : x)),
    });

  async function importCsv(file: File) {
    setError('');
    try {
      const imported = await api.importQuestions(file);
      onChange({ ...value, questions: [...value.questions, ...imported] });
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-muted-foreground text-sm">
          The planner picks from this bank to fit the interview length. CSV columns: text, category,
          difficulty, expected_signals (separated by ;).
        </p>
        <div className="flex gap-2">
          <input
            ref={fileRef}
            type="file"
            accept=".csv,text/csv"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) importCsv(f);
              e.target.value = '';
            }}
          />
          <Button variant="outline" size="sm" onClick={() => fileRef.current?.click()}>
            <UploadSimpleIcon /> Import CSV
          </Button>
          <Button
            size="sm"
            onClick={() =>
              onChange({
                ...value,
                questions: [
                  ...value.questions,
                  { text: '', category: '', difficulty: 'medium', expected_signals: [] },
                ],
              })
            }
          >
            <PlusIcon /> Add question
          </Button>
        </div>
      </div>
      <ErrorText>{error}</ErrorText>
      {value.questions.length === 0 && (
        <Card className="text-muted-foreground text-center text-sm">No questions yet.</Card>
      )}
      {value.questions.map((q, i) => (
        <Card key={i} className="grid gap-3 md:grid-cols-[1fr_10rem_8rem_auto]">
          <Field label={`Question ${i + 1}`} className="md:col-span-4">
            <Textarea
              className="min-h-16"
              value={q.text}
              onChange={(e) => set(i, { text: e.target.value })}
            />
          </Field>
          <Field label="Expected signals" hint="Optional; one per line">
            <Textarea
              className="min-h-9"
              value={q.expected_signals.join('\n')}
              onChange={(e) => set(i, { expected_signals: e.target.value.split('\n') })}
            />
          </Field>
          <Field label="Category">
            <Input value={q.category} onChange={(e) => set(i, { category: e.target.value })} />
          </Field>
          <Field label="Difficulty">
            <select
              className="border-input bg-background h-9 rounded-md border px-2 text-sm"
              value={q.difficulty}
              onChange={(e) => set(i, { difficulty: e.target.value })}
            >
              <option value="">—</option>
              <option value="easy">easy</option>
              <option value="medium">medium</option>
              <option value="hard">hard</option>
            </select>
          </Field>
          <Button
            variant="ghost"
            size="icon"
            className="self-end"
            aria-label={`Remove question ${i + 1}`}
            onClick={() =>
              onChange({ ...value, questions: value.questions.filter((_, k) => k !== i) })
            }
          >
            <TrashIcon />
          </Button>
        </Card>
      ))}
    </div>
  );
}

const slug = (s: string) =>
  s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_|_$/g, '') || 'criterion';

const DEFAULT_ANCHORS: Record<string, string> = {
  '1': 'No evidence of the skill.',
  '2': 'Basic, with major gaps.',
  '3': 'Solid; covers the main points.',
  '4': 'Strong; explains trade-offs.',
  '5': 'Excellent; anticipates edge cases.',
};

export function CriteriaSection({ value, onChange }: SectionProps) {
  const set = (i: number, c: Partial<Criterion>) =>
    onChange({
      ...value,
      criteria: value.criteria.map((x, k) => {
        if (k !== i) return x;
        const next = { ...x, ...c };
        return c.name !== undefined ? { ...next, id: slug(c.name) } : next; // id follows the name
      }),
    });
  const total = value.criteria.reduce((s, c) => s + (c.weight || 0), 0);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-muted-foreground text-sm">
          Each criterion is scored 1 to 5 against its anchors; the overall score is the weighted
          mean.
        </p>
        <Button
          size="sm"
          onClick={() =>
            onChange({
              ...value,
              criteria: [
                ...value.criteria,
                {
                  id: `criterion_${value.criteria.length + 1}`,
                  name: '',
                  description: '',
                  weight: 1,
                  anchors: { ...DEFAULT_ANCHORS },
                },
              ],
            })
          }
        >
          <PlusIcon /> Add criterion
        </Button>
      </div>
      {value.criteria.length === 0 && (
        <Card className="text-muted-foreground text-center text-sm">No criteria yet.</Card>
      )}
      {value.criteria.map((c, i) => (
        <Card key={i} className="flex flex-col gap-3">
          <div className="grid gap-3 md:grid-cols-[1fr_8rem_auto]">
            <Field label="Name">
              <Input value={c.name} onChange={(e) => set(i, { name: e.target.value })} />
            </Field>
            <Field
              label="Weight"
              hint={
                total ? `${Math.round(((c.weight || 0) / total) * 100)}% of overall` : undefined
              }
            >
              <Input
                type="number"
                min={0.1}
                step={0.5}
                value={c.weight}
                onChange={(e) => set(i, { weight: Number(e.target.value) })}
              />
            </Field>
            <Button
              variant="ghost"
              size="icon"
              className="mt-6"
              aria-label={`Remove criterion ${c.name || i + 1}`}
              onClick={() =>
                onChange({ ...value, criteria: value.criteria.filter((_, k) => k !== i) })
              }
            >
              <TrashIcon />
            </Button>
          </div>
          <Field label="What it measures">
            <Input
              value={c.description}
              onChange={(e) => set(i, { description: e.target.value })}
            />
          </Field>
          <div className="grid gap-2 md:grid-cols-5">
            {['1', '2', '3', '4', '5'].map((k) => (
              <Field key={k} label={`Score ${k}`}>
                <Textarea
                  className="min-h-20 text-xs"
                  value={c.anchors[k] ?? ''}
                  onChange={(e) => set(i, { anchors: { ...c.anchors, [k]: e.target.value } })}
                />
              </Field>
            ))}
          </div>
        </Card>
      ))}
    </div>
  );
}
