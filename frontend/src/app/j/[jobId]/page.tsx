import type { Metadata } from 'next';
import { JobView } from '@/components/job-view';

export const metadata: Metadata = {
  title: 'Reconstruction — Driftless',
};

export default async function JobPage({ params }: { params: Promise<{ jobId: string }> }) {
  const { jobId } = await params;
  return (
    <main className="relative min-h-dvh overflow-x-hidden">
      <JobView jobId={jobId} />
    </main>
  );
}
