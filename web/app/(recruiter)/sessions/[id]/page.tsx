import { SessionReview } from '@/components/guru/session-review';

export default async function SessionPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <SessionReview id={id} />;
}
