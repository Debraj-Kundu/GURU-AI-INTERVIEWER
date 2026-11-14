'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
  ArrowLeftIcon,
  BriefcaseIcon,
  PencilSimpleIcon,
  PlusIcon,
  TrashIcon,
} from '@phosphor-icons/react';
import { Avatar, ErrorText, SectionTitle, Skeleton } from '@/components/guru/ui';
import { type Company, type JobSummary, api } from '@/lib/api';
import { cn } from '@/lib/shadcn/utils';

/** One company's roles. `refresh` changes after a save so titles stay current. */
export function RolesSidebar({
  companyId,
  activeId,
  refresh,
}: {
  companyId: string;
  activeId?: string;
  refresh?: unknown;
}) {
  const router = useRouter();
  const [company, setCompany] = useState<Company | null>(null);
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [error, setError] = useState('');

  useEffect(() => {
    api.getCompany(companyId).then(setCompany, (e) => setError(e.message));
    api.listJobs(companyId).then(setJobs, (e) => setError(e.message));
  }, [companyId, refresh]);

  async function rename() {
    const name = prompt('Company name', company?.name)?.trim();
    if (!name || name === company?.name) return;
    try {
      setCompany(await api.renameCompany(companyId, name));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function remove() {
    if (!confirm(`Delete ${company?.name} with all its roles, sources, plans, and interviews?`))
      return;
    try {
      await api.deleteCompany(companyId);
      router.push('/');
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const action =
    'text-muted-foreground hover:text-foreground hover:bg-accent flex cursor-pointer items-center gap-1 rounded-md px-1.5 py-1 transition-colors';

  return (
    <nav aria-label="Roles" className="animate-rise flex flex-col gap-6">
      <Link
        href="/"
        className="text-muted-foreground hover:text-foreground group flex items-center gap-1.5 text-sm transition-colors"
      >
        <ArrowLeftIcon className="transition-transform group-hover:-translate-x-0.5" /> All
        companies
      </Link>
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-3">
          {company ? (
            <Avatar name={company.name} className="size-9 rounded-lg" />
          ) : (
            <Skeleton className="size-9 rounded-lg" />
          )}
          {company ? (
            <h2 className="min-w-0 truncate font-semibold tracking-tight">{company.name}</h2>
          ) : (
            <Skeleton className="h-4 w-24" />
          )}
        </div>
        <div className="-ml-1.5 flex gap-1 text-xs">
          <button type="button" className={action} onClick={rename}>
            <PencilSimpleIcon /> Rename
          </button>
          <button type="button" className={cn(action, 'hover:text-destructive')} onClick={remove}>
            <TrashIcon /> Delete
          </button>
        </div>
      </div>
      <div className="flex flex-col gap-2">
        <SectionTitle>Roles</SectionTitle>
        <ul className="flex flex-col gap-0.5">
          {jobs.map((j) => (
            <li key={j.id}>
              <Link
                href={`/jobs/${j.id}`}
                aria-current={j.id === activeId ? 'page' : undefined}
                className={cn(
                  'flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors',
                  j.id === activeId
                    ? 'bg-accent text-accent-foreground font-medium'
                    : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground'
                )}
              >
                <BriefcaseIcon
                  className={cn('shrink-0', j.id === activeId && 'text-primary')}
                  weight={j.id === activeId ? 'fill' : 'regular'}
                />
                <span className="truncate">{j.title}</span>
              </Link>
            </li>
          ))}
        </ul>
      </div>
      <Link
        href={`/companies/${companyId}/new`}
        className={cn(
          'hover:border-primary/50 hover:text-primary flex items-center justify-center gap-2 rounded-md border border-dashed py-2 text-sm font-medium transition-colors',
          !activeId ? 'border-primary/50 text-primary bg-primary/5' : 'text-muted-foreground'
        )}
      >
        <PlusIcon weight="bold" /> New role
      </Link>
      <ErrorText>{error}</ErrorText>
    </nav>
  );
}
