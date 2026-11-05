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
import { RolesSidebar } from '@/components/guru/roles-sidebar';
import { Card, ErrorText, PageHeader, Tabs } from '@/components/guru/ui';
import { Button } from '@/components/ui/button';
import { type Job, type JobInput, type PlanState, api } from '@/lib/api';

type TabId = 'details' | 'questions' | 'criteria' | 'kb' | 'plan';

const toInput = ({ title, jd_text, max_duration_sec, questions, criteria }: Job): JobInput => ({
  title,
  jd_text,
  max_duration_sec,
  questions,
  criteria,
});

/** A role's page. Without `jobId` it's the "new role" page for `companyId` (Create instead of Save,
 *  no plan yet). Roles share their company's name and knowledge base. */
export function JobWorkspace({ jobId, companyId: newFor }: { jobId?: string; companyId?: string }) {
  const router = useRouter();
  const [companyId, setCompanyId] = useState(newFor);
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
        setCompanyId(j.company_id);
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
        const job = await api.createJob(companyId!, body);
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
    if (!jobId || !confirm('Delete this role, its plan, and its interviews?')) return;
    try {
      await api.deleteJob(jobId);
      router.push(`/companies/${companyId}`);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (!saved || !companyId) return <ErrorText>{error}</ErrorText>;

  const tabs: { id: TabId; label: string }[] = [
    { id: 'details', label: 'Details' },
    { id: 'questions', label: `Questions (${form.questions.length})` },
    { id: 'criteria', label: `Criteria (${form.criteria.length})` },
    { id: 'kb', label: 'Knowledge base' },
    ...(jobId ? ([{ id: 'plan', label: 'Plan' }] as const) : []),
  ];
  const canCreateInterview = !dirty && plan?.status === 'ready' && !plan.stale;

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-8 lg:grid-cols-[200px_minmax(0,1fr)_340px] lg:gap-0">
      <aside className="lg:sticky lg:top-24 lg:self-start lg:pr-6">
        <RolesSidebar companyId={companyId} activeId={jobId} refresh={saved} />
      </aside>
      <div className="flex min-w-0 flex-col gap-5 lg:border-x lg:px-8">
        <PageHeader
          eyebrow={jobId ? 'Role' : 'New role'}
          title={saved.title || 'New role'}
          actions={
            <>
              {jobId && (
                <Button variant="ghost" onClick={remove}>
                  Delete
                </Button>
              )}
              <Button onClick={save} disabled={saving || (!!jobId && !dirty)}>
                {saving ? 'Saving…' : jobId ? (dirty ? 'Save changes' : 'Saved') : 'Create role'}
              </Button>
            </>
          }
        />
        <ErrorText>{error}</ErrorText>
        <Tabs tabs={tabs} value={tab} onChange={setTab} />
        {tab === 'details' && <DetailsSection value={form} onChange={setForm} />}
        {tab === 'questions' && <QuestionsSection value={form} onChange={setForm} />}
        {tab === 'criteria' && <CriteriaSection value={form} onChange={setForm} />}
        {tab === 'kb' && <KnowledgeBasePanel companyId={companyId} />}
        {jobId && tab === 'plan' && (
          <PlanPanel jobId={jobId} dirty={dirty} onPlanChange={setPlan} />
        )}
      </div>
      <aside className="flex flex-col gap-4 lg:pl-6" aria-label="Interviews">
        <h2 className="text-lg font-semibold tracking-tight">Interviews</h2>
        {jobId ? (
          <InterviewsPanel jobId={jobId} canCreate={canCreateInterview} />
        ) : (
          <Card className="text-muted-foreground p-4 text-sm">
            Create the role to invite candidates.
          </Card>
        )}
      </aside>
    </div>
  );
}
