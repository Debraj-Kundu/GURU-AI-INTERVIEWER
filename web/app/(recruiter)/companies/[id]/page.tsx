'use client';

import { use, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { JobWorkspace } from '@/components/guru/job-workspace';
import { ErrorText } from '@/components/guru/ui';
import { api } from '@/lib/api';

/** A company opens on its first role; with no roles yet it shows the new-role form. */
export default function CompanyPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const [empty, setEmpty] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    api.listJobs(id).then(
      (jobs) => (jobs.length ? router.replace(`/jobs/${jobs[0].id}`) : setEmpty(true)),
      (e) => setError(e.message)
    );
  }, [id, router]);

  if (error) return <ErrorText>{error}</ErrorText>;
  return empty ? <JobWorkspace companyId={id} /> : null;
}
