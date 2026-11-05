'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ArrowUpRightIcon, PlusIcon } from '@phosphor-icons/react';
import { Card, ErrorText, Input, PageHeader } from '@/components/guru/ui';
import { Button } from '@/components/ui/button';
import { type Company, api } from '@/lib/api';

export default function CompaniesPage() {
  const router = useRouter();
  const [companies, setCompanies] = useState<Company[] | null>(null);
  const [name, setName] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    api.listCompanies().then(setCompanies, (e) => setError(e.message));
  }, []);

  async function create() {
    try {
      const c = await api.createCompany(name.trim());
      router.push(`/companies/${c.id}/new`); // a new company starts with its first role
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div className="flex flex-col gap-8">
      <PageHeader
        eyebrow="Recruiter / Companies"
        title="Companies"
        subtitle="Each company's roles share its name and knowledge base."
        actions={
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              create();
            }}
          >
            <Input
              className="w-56"
              placeholder="Company name"
              aria-label="New company name"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <Button type="submit" disabled={!name.trim()}>
              <PlusIcon /> New company
            </Button>
          </form>
        }
      />
      <ErrorText>{error && `Couldn't load companies: ${error}. Is the API running?`}</ErrorText>
      {companies?.length === 0 && (
        <Card className="text-muted-foreground text-center">
          No companies yet. Add one to upload its documents, create roles, and run interviews.
        </Card>
      )}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {companies?.map((c) => (
          <Link key={c.id} href={`/companies/${c.id}`} className="group">
            <Card className="group-hover:border-primary flex h-full flex-col gap-1 transition-all group-hover:-translate-y-0.5">
              <div className="flex items-start justify-between gap-3">
                <h2 className="text-lg font-semibold tracking-tight">{c.name}</h2>
                <ArrowUpRightIcon className="text-muted-foreground group-hover:text-primary mt-1 shrink-0 transition-colors" />
              </div>
              <p className="text-muted-foreground mt-auto border-t pt-3 font-mono text-[11px] tracking-wide uppercase">
                {c.roles} role{c.roles === 1 ? '' : 's'} · {c.sources} source
                {c.sources === 1 ? '' : 's'}
              </p>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
