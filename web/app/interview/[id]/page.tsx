import { ThemeToggle } from '@/components/app/theme-toggle';
import { InterviewApp } from '@/components/interview/interview-app';

// Candidate page reached by the recruiter's link. No score or plan is ever shown here (PLAN Q8).
export default async function InterviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <>
      <InterviewApp id={id} />
      <div className="group fixed bottom-0 left-1/2 z-50 mb-2 -translate-x-1/2">
        <ThemeToggle className="translate-y-20 transition-transform delay-150 duration-300 group-hover:translate-y-0" />
      </div>
    </>
  );
}
