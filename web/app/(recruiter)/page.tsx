'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
  ArrowRightIcon,
  BooksIcon,
  BriefcaseIcon,
  BuildingsIcon,
  PlusIcon,
} from '@phosphor-icons/react';
import {
  Avatar,
  Card,
  EmptyState,
  ErrorText,
  Input,
  PageHeader,
  Skeleton,
} from '@/components/guru/ui';
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

  const roles = companies?.reduce((n, c) => n + c.roles, 0) ?? 0;
  const sources = companies?.reduce((n, c) => n + c.sources, 0) ?? 0;

  return (
    <div className="flex flex-col gap-8">
      <PageHeader
        eyebrow={
          <>
            <BuildingsIcon weight="bold" /> Workspace
          </>
        }
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
              <PlusIcon weight="bold" /> New company
            </Button>
          </form>
        }
      />

      {companies && companies.length > 0 && (
        <div className="grid gap-4 sm:grid-cols-3">
          {[
            { label: 'Companies', value: companies.length, Icon: BuildingsIcon },
            { label: 'Open roles', value: roles, Icon: BriefcaseIcon },
            { label: 'Knowledge sources', value: sources, Icon: BooksIcon },
          ].map(({ label, value, Icon }, i) => (
            <Card
              key={label}
              className="animate-rise relative flex items-center gap-4 overflow-hidden"
              style={{ animationDelay: `${i * 60}ms` }}
            >
              <span className="bg-primary/10 text-primary grid size-10 place-items-center rounded-lg">
                <Icon size={20} weight="duotone" />
              </span>
              <div>
                <p className="text-muted-foreground text-sm">{label}</p>
                <p className="text-2xl font-semibold tabular-nums">{value}</p>
              </div>
              <svg
                viewBox="0 0 100 30"
                preserveAspectRatio="none"
                aria-hidden
                className="text-primary/15 absolute right-0 bottom-0 h-12 w-40"
              >
                <path
                  d="M0 26 C 15 22, 25 10, 40 14 S 65 24, 75 12 S 92 4, 100 6 L100 30 L0 30Z"
                  fill="currentColor"
                />
              </svg>
            </Card>
          ))}
        </div>
      )}

      <ErrorText>{error && `Couldn't load companies: ${error}. Is the API running?`}</ErrorText>

      {companies?.length === 0 && (
        <EmptyState
          title="No companies yet"
          body="Add one to upload its documents, create roles, and run interviews."
        />
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {!companies &&
          !error &&
          Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-36 rounded-xl" />)}
        {companies?.map((c, i) => (
          <Link
            key={c.id}
            href={`/companies/${c.id}`}
            className="group animate-rise rounded-xl outline-none"
            style={{ animationDelay: `${120 + i * 40}ms` }}
          >
            <Card className="group-hover:border-primary/40 group-hover:shadow-primary/10 group-focus-visible:ring-ring/50 flex h-full flex-col gap-4 transition-all duration-200 group-hover:-translate-y-0.5 group-hover:shadow-lg group-focus-visible:ring-[3px]">
              <div className="flex items-center gap-3">
                <Avatar name={c.name} className="size-10 rounded-lg text-sm" />
                <h2 className="min-w-0 flex-1 truncate font-semibold tracking-tight">{c.name}</h2>
                <ArrowRightIcon className="text-muted-foreground group-hover:text-primary shrink-0 -translate-x-1 opacity-0 transition-all group-hover:translate-x-0 group-hover:opacity-100" />
              </div>
              <div className="text-muted-foreground mt-auto flex gap-4 border-t pt-3 text-xs">
                <span className="flex items-center gap-1.5">
                  <BriefcaseIcon size={14} /> {c.roles} role{c.roles === 1 ? '' : 's'}
                </span>
                <span className="flex items-center gap-1.5">
                  <BooksIcon size={14} /> {c.sources} source{c.sources === 1 ? '' : 's'}
                </span>
              </div>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
