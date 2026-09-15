import type { Metadata } from 'next';
import { BatchView } from '@/components/batch/batch-view';

export const metadata: Metadata = {
  title: 'Extracting — LeadForge',
};

/**
 * The batch screen is driven entirely by a live SSE stream, so the server component's only
 * job is to hand the id to the client view.
 */
export default async function BatchPage({ params }: { params: Promise<{ batchId: string }> }) {
  const { batchId } = await params;
  return <BatchView batchId={batchId} />;
}
