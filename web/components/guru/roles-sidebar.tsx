'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { PlusIcon } from '@phosphor-icons/react';
import { ErrorText } from '@/components/guru/ui';
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

  const action = 'text-muted-foreground hover:text-primary cursor-pointer';

  return (
    <nav aria-label="Roles" className="flex flex-col gap-6">
      <Link
        href="/"
        className="text-muted-foreground hover:text-primary font-mono text-[11px] tracking-widest uppercase"
      >
        ← All companies
      </Link>
      <div className="flex flex-col gap-2">
        <h2 className="text-lg font-semibold tracking-tight">{company?.name}</h2>
        <div className="flex gap-3 text-xs">
          <button type="button" className={action} onClick={rename}>
            Rename
          </button>
          <button type="button" className={action} onClick={remove}>
            Delete
          </button>
        </div>
      </div>
      <div className="flex flex-col gap-2">
        <p className="text-muted-foreground font-mono text-[11px] tracking-widest uppercase">
          Roles
        </p>
        <ul className="flex flex-col">
          {jobs.map((j) => (
            <li key={j.id}>
              <Link
                href={`/jobs/${j.id}`}
                aria-current={j.id === activeId ? 'page' : undefined}
                className={cn(
                  'block border-l-2 py-1.5 pl-3 text-sm transition-colors',
                  j.id === activeId
                    ? 'border-primary text-foreground font-medium'
                    : 'text-muted-foreground hover:text-foreground border-transparent'
                )}
              >
                {j.title}
              </Link>
            </li>
          ))}
        </ul>
      </div>
      <Link
        href={`/companies/${companyId}/new`}
        className={cn(
          'hover:text-primary flex items-center gap-2 font-mono text-xs tracking-wider uppercase',
          !activeId ? 'text-primary' : 'text-muted-foreground'
        )}
      >
        <PlusIcon /> New role
      </Link>
      <ErrorText>{error}</ErrorText>
    </nav>
  );
}
