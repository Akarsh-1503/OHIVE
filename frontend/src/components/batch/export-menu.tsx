'use client';

import { ChevronDown, Download, FileSpreadsheet, FileText, Loader2 } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { downloadExport } from '@/lib/api';
import { Button, Checkbox, Popover } from '../ui';

export function ExportMenu({
  batchId,
  disabled,
  onExported,
}: {
  batchId: string;
  disabled: boolean;
  onExported: () => void;
}) {
  const [includeLowConfidence, setIncludeLowConfidence] = useState(true);
  const [includeDuplicates, setIncludeDuplicates] = useState(false);
  const [busy, setBusy] = useState<'xlsx' | 'csv' | null>(null);

  const run = async (format: 'xlsx' | 'csv'): Promise<void> => {
    setBusy(format);
    try {
      const filename = await downloadExport(batchId, format, {
        includeLowConfidence,
        includeDuplicates,
      });
      onExported();
      toast.success('Export ready', { description: `${filename} downloaded.` });
    } catch {
      toast.error('Export failed', { description: 'The batch could not be exported.' });
    } finally {
      setBusy(null);
    }
  };

  return (
    <Popover
      label="Export options"
      trigger={({ toggle, open }) => (
        <Button variant="primary" size="sm" onClick={toggle} disabled={disabled} aria-expanded={open}>
          <Download className="h-3.5 w-3.5" aria-hidden />
          Export
          <ChevronDown className="h-3.5 w-3.5 opacity-70" aria-hidden />
        </Button>
      )}
    >
      {() => (
        <div className="space-y-3">
          <div className="text-[11px] font-medium uppercase tracking-[0.16em] text-ink-muted">
            Rows to include
          </div>
          <Checkbox
            checked={includeLowConfidence}
            onChange={setIncludeLowConfidence}
            label="Low-confidence rows"
          />
          <Checkbox
            checked={includeDuplicates}
            onChange={setIncludeDuplicates}
            label="Duplicate cards"
          />
          <div className="h-px bg-line" />
          <div className="flex flex-col gap-2">
            <Button size="sm" variant="primary" onClick={() => void run('xlsx')} disabled={busy !== null}>
              {busy === 'xlsx' ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : (
                <FileSpreadsheet className="h-3.5 w-3.5" aria-hidden />
              )}
              Download .xlsx
            </Button>
            <Button size="sm" variant="outline" onClick={() => void run('csv')} disabled={busy !== null}>
              {busy === 'csv' ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : (
                <FileText className="h-3.5 w-3.5" aria-hidden />
              )}
              Download .csv
            </Button>
          </div>
        </div>
      )}
    </Popover>
  );
}
