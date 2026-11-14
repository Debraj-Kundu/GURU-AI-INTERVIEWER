'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  BooksIcon,
  CheckIcon,
  CircleNotchIcon,
  FileTextIcon,
  ListChecksIcon,
  ListNumbersIcon,
  SparkleIcon,
  TrashIcon,
  UsersIcon,
} from '@phosphor-icons/react';
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
import { Count, EmptyState, ErrorText, PageHeader, Skeleton, Tabs } from '@/components/guru/ui';
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

  if (!saved || !companyId)
    return error ? (
      <ErrorText>{error}</ErrorText>
    ) : (
      <div className="grid gap-6 lg:grid-cols-[220px_1fr_360px]">
        <Skeleton className="h-64" />
        <Skeleton className="h-96" />
        <Skeleton className="h-64" />
      </div>
    );

  const tabs: { id: TabId; label: React.ReactNode }[] = [
    {
      id: 'details',
      label: (
        <>
          <FileTextIcon /> Details
        </>
      ),
    },
    {
      id: 'questions',
      label: (
        <>
          <ListNumbersIcon /> Questions <Count n={form.questions.length} />
        </>
      ),
    },
    {
      id: 'criteria',
      label: (
        <>
          <ListChecksIcon /> Criteria <Count n={form.criteria.length} />
        </>
      ),
    },
    {
      id: 'kb',
      label: (
        <>
          <BooksIcon /> Knowledge base
        </>
      ),
    },
    ...(jobId
      ? [
          {
            id: 'plan' as const,
            label: (
              <>
                <SparkleIcon /> Plan
              </>
            ),
          },
        ]
      : []),
  ];
  const canCreateInterview = !dirty && plan?.status === 'ready' && !plan.stale;

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-8 lg:grid-cols-[220px_minmax(0,1fr)_360px] lg:gap-0">
      <aside className="lg:sticky lg:top-22 lg:self-start lg:pr-6">
        <RolesSidebar companyId={companyId} activeId={jobId} refresh={saved} />
      </aside>
      <div className="flex min-w-0 flex-col gap-5 lg:border-x lg:px-8">
        <PageHeader
          eyebrow={jobId ? 'Role' : 'New role'}
          subtitle={
            jobId &&
            (dirty ? (
              <span className="flex items-center gap-1.5 text-amber-600 dark:text-amber-400">
                <span className="size-1.5 animate-pulse rounded-full bg-current" /> Unsaved changes
              </span>
            ) : (
              <span className="flex items-center gap-1.5">
                <CheckIcon className="text-emerald-500" weight="bold" /> All changes saved
              </span>
            ))
          }
          title={saved.title || 'New role'}
          actions={
            <>
              {jobId && (
                <Button
                  variant="ghost"
                  onClick={remove}
                  className="hover:text-destructive hover:bg-destructive/10"
                >
                  <TrashIcon /> Delete
                </Button>
              )}
              <Button onClick={save} disabled={saving || (!!jobId && !dirty)}>
                {saving && <CircleNotchIcon className="animate-spin" />}
                {saving ? 'Saving…' : jobId ? (dirty ? 'Save changes' : 'Saved') : 'Create role'}
              </Button>
            </>
          }
        />
        <ErrorText>{error}</ErrorText>
        <Tabs tabs={tabs} value={tab} onChange={setTab} />
        <div key={tab} className="animate-rise flex flex-col gap-5">
          {tab === 'details' && <DetailsSection value={form} onChange={setForm} />}
          {tab === 'questions' && <QuestionsSection value={form} onChange={setForm} />}
          {tab === 'criteria' && <CriteriaSection value={form} onChange={setForm} />}
          {tab === 'kb' && <KnowledgeBasePanel companyId={companyId} />}
          {jobId && tab === 'plan' && (
            <PlanPanel jobId={jobId} dirty={dirty} onPlanChange={setPlan} />
          )}
        </div>
      </div>
      <aside className="flex flex-col gap-4 lg:pl-6" aria-label="Interviews">
        <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight">
          <UsersIcon className="text-primary" weight="duotone" /> Interviews
        </h2>
        {jobId ? (
          <InterviewsPanel jobId={jobId} canCreate={canCreateInterview} />
        ) : (
          <EmptyState title="No interviews yet" body="Create the role to invite candidates." />
        )}
      </aside>
    </div>
  );
}
