'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { Card, ErrorText } from '@/components/guru/ui';
import { Button } from '@/components/ui/button';
import { type JobSummary, api } from '@/lib/api';

export default function JobsPage() {
  const [jobs, setJobs] = useState<JobSummary[] | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    api.listJobs().then(setJobs, (e) => setError(e.message));
  }, []);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Jobs</h1>
        <Button asChild>
          <Link href="/jobs/new">New job</Link>
        </Button>
      </div>
      <ErrorText>{error && `Couldn't load jobs: ${error}. Is the API running?`}</ErrorText>
      {jobs?.length === 0 && (
        <Card className="text-muted-foreground text-center">
          No jobs yet. Create one to upload company documents, plan questions, and run interviews.
        </Card>
      )}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {jobs?.map((j) => (
          <Link key={j.id} href={`/jobs/${j.id}`}>
            <Card className="hover:border-ring h-full transition-colors">
              <h2 className="font-medium">{j.title}</h2>
              <p className="text-muted-foreground text-sm">{j.company_name}</p>
              <p className="text-muted-foreground mt-3 text-xs">
                {j.questions} questions · {j.criteria} criteria · updated{' '}
                {new Date(j.updated_at).toLocaleDateString()}
              </p>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
