'use client';

import { Download, FileJson, FileText, Boxes } from 'lucide-react';
import { toast } from 'sonner';
import { downloadExport, explain, type ExportKind } from '@/lib/api';
import { Button } from './ui';

const ITEMS: { kind: ExportKind; label: string; icon: typeof Download; blurb: string }[] = [
  {
    kind: 'ply',
    label: 'Point cloud (.ply)',
    icon: Boxes,
    blurb: 'Full-resolution cloud with per-point colour — opens in MeshLab, CloudCompare or Open3D.',
  },
  {
    kind: 'tum',
    label: 'Trajectory (TUM)',
    icon: FileText,
    blurb: 'One line per frame: timestamp tx ty tz qx qy qz qw. The format evo and the TUM tools expect.',
  },
  {
    kind: 'report.json',
    label: 'Report (.json)',
    icon: FileJson,
    blurb: 'Every metric plus the run configuration, for pasting straight into a write-up.',
  },
];

export function Exports({ jobId }: { jobId: string }) {
  const run = async (kind: ExportKind) => {
    try {
      await downloadExport(jobId, kind);
    } catch (e: unknown) {
      const { title, detail } = explain(e);
      toast.error(title, { description: detail ?? undefined });
    }
  };

  return (
    <ul className="space-y-2">
      {ITEMS.map((it) => (
        <li key={it.kind}>
          <Button variant="subtle" size="sm" icon={it.icon} onClick={() => void run(it.kind)} className="w-full justify-start">
            {it.label}
          </Button>
          <p className="mt-1 text-[0.65rem] leading-snug text-ink-faint">{it.blurb}</p>
        </li>
      ))}
    </ul>
  );
}
