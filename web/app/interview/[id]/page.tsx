import { InterviewApp } from '@/components/interview/interview-app';

// Candidate page reached by the recruiter's link. No score or plan is ever shown here (PLAN Q8).
export default async function InterviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <InterviewApp id={id} />;
}
