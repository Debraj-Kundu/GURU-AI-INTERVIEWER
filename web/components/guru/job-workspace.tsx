'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { InterviewsPanel } from '@/components/guru/interviews-panel';
import {
  CriteriaSection,
  DetailsSection,
  EMPTY_JOB,
  QuestionsSection,
  jobProblems,
  normalizeJob,
} from '@/components/guru/job-form';
import { KnowledgeBasePanel } from '@/components/guru/kb-panel';
import { PlanPanel } from '@/components/guru/plan-panel';
import { ErrorText, Tabs } from '@/components/guru/ui';
import { Button } from '@/components/ui/button';
import { type Job, type JobInput, type PlanState, api } from '@/lib/api';

type TabId = 'details' | 'questions' | 'criteria' | 'kb' | 'plan' | 'interviews';

const toInput = ({
  title,
  company_name,
  jd_text,
  max_duration_sec,
  questions,
  criteria,
}: Job): JobInput => ({
  title,
  company_name,
  jd_text,
  max_duration_sec,
  questions,
  criteria,
});

/** `jobId` undefined = the "new job" page (only the editable tabs, Create instead of Save). */
export function JobWorkspace({ jobId }: { jobId?: string }) {
  const router = useRouter();
  const [saved, setSaved] = useState<JobInput | null>(jobId ? null : EMPTY_JOB);
  const [form, setForm] = useState<JobInput>(EMPTY_JOB);
  const [tab, setTab] = useState<TabId>('details');
  const [plan, setPlan] = useState<PlanState | null>(null);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!jobId) return;
    api.getJob(jobId).then(
      (j) => {
        setSaved(toInput(j));
        setForm(toInput(j));
      },
      (e) => setError(e.message)
    );
    api.getPlan(jobId).then(setPlan, () => {});
  }, [jobId]);

  const dirty = JSON.stringify(form) !== JSON.stringify(saved);

  async function save() {
    const problems = jobProblems(form);
    if (problems.length) return setError(problems.join(' '));
    setError('');
    setSaving(true);
    try {
      const body = normalizeJob(form);
      if (!jobId) {
        const job = await api.createJob(body);
        router.push(`/jobs/${job.id}`);
        return;
      }
      const job = await api.updateJob(jobId, body);
      setSaved(toInput(job));
      setForm(toInput(job));
      setPlan(await api.getPlan(jobId)); // the save made any existing plan stale
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    if (!jobId || !confirm('Delete this job, its sources, plan, and interviews?')) return;
    try {
      await api.deleteJob(jobId);
      router.push('/jobs');
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (!saved) return <ErrorText>{error}</ErrorText>;

  const tabs: { id: TabId; label: string }[] = [
    { id: 'details', label: 'Details' },
    { id: 'questions', label: `Questions (${form.questions.length})` },
    { id: 'criteria', label: `Criteria (${form.criteria.length})` },
    ...(jobId
      ? ([
          { id: 'kb', label: 'Knowledge base' },
          { id: 'plan', label: 'Plan' },
          { id: 'interviews', label: 'Interviews' },
        ] as const)
      : []),
  ];
  const canCreateInterview = !dirty && plan?.status === 'ready' && !plan.stale;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">{saved.title || 'New job'}</h1>
          {saved.company_name && (
            <p className="text-muted-foreground text-sm">{saved.company_name}</p>
          )}
        </div>
        <div className="flex gap-2">
          {jobId && (
            <Button variant="ghost" onClick={remove}>
              Delete
            </Button>
          )}
          <Button onClick={save} disabled={saving || (!!jobId && !dirty)}>
            {saving ? 'Saving…' : jobId ? (dirty ? 'Save changes' : 'Saved') : 'Create job'}
          </Button>
        </div>
      </div>
      <ErrorText>{error}</ErrorText>
      <Tabs tabs={tabs} value={tab} onChange={setTab} />
      {tab === 'details' && <DetailsSection value={form} onChange={setForm} />}
      {tab === 'questions' && <QuestionsSection value={form} onChange={setForm} />}
      {tab === 'criteria' && <CriteriaSection value={form} onChange={setForm} />}
      {jobId && tab === 'kb' && <KnowledgeBasePanel jobId={jobId} />}
      {jobId && tab === 'plan' && <PlanPanel jobId={jobId} dirty={dirty} onPlanChange={setPlan} />}
      {jobId && tab === 'interviews' && (
        <InterviewsPanel jobId={jobId} canCreate={canCreateInterview} />
      )}
    </div>
  );
}
