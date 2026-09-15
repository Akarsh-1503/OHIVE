'use client';

import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnFiltersState,
  type SortingState,
  type VisibilityState,
} from '@tanstack/react-table';
import { motion } from 'framer-motion';
import { ArrowDown, ArrowUp, Columns3, Copy, Search, TriangleAlert } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useAllLeads } from '@/lib/store';
import { FIELD_LABELS, type Lead } from '@/lib/types';
import { ConfidenceBar } from '../metrics';
import { Badge, Button, Checkbox, Popover, cx } from '../ui';

const helper = createColumnHelper<Lead>();

const STATUS_TONE: Record<Lead['status'], 'neutral' | 'accent' | 'ok' | 'warn' | 'bad'> = {
  queued: 'neutral',
  processing: 'accent',
  completed: 'ok',
  needs_review: 'warn',
  failed: 'bad',
};

export function LeadTable({ onOpen }: { onOpen: (cardId: string) => void }) {
  const leads = useAllLeads();
  const [sorting, setSorting] = useState<SortingState>([]);
  const [globalFilter, setGlobalFilter] = useState('');
  const [rowSelection, setRowSelection] = useState<Record<string, boolean>>({});
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({
    location: false,
    website: false,
  });
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);
  const [groupDuplicates, setGroupDuplicates] = useState(true);

  const data = useMemo(() => {
    if (!groupDuplicates) return leads;
    // Park each duplicate directly beneath the card it duplicates.
    const rank = new Map(leads.map((lead, index) => [lead.card_id, index]));
    return [...leads].sort((a, b) => {
      const keyA = rank.get(a.duplicate_of ?? a.card_id) ?? 0;
      const keyB = rank.get(b.duplicate_of ?? b.card_id) ?? 0;
      if (keyA !== keyB) return keyA - keyB;
      return (a.duplicate_of ? 1 : 0) - (b.duplicate_of ? 1 : 0);
    });
  }, [leads, groupDuplicates]);

  const columns = useMemo(
    () => [
      helper.display({
        id: 'select',
        header: ({ table }) => (
          <Checkbox
            checked={table.getIsAllRowsSelected()}
            indeterminate={table.getIsSomeRowsSelected()}
            onChange={(next) => table.toggleAllRowsSelected(next)}
            label={<span className="sr-only">Select all rows</span>}
          />
        ),
        cell: ({ row }) => (
          <span onClick={(event) => event.stopPropagation()}>
            <Checkbox
              checked={row.getIsSelected()}
              onChange={(next) => row.toggleSelected(next)}
              label={<span className="sr-only">Select {row.original.filename}</span>}
            />
          </span>
        ),
        enableSorting: false,
      }),
      helper.accessor((row) => [row.first_name, row.last_name].filter(Boolean).join(' '), {
        id: 'name',
        header: 'Name',
        cell: (info) => (
          <div className="flex items-center gap-2">
            <span className="truncate font-medium text-ink">{info.getValue() || '—'}</span>
            {info.row.original.duplicate_of ? (
              <Copy className="h-3 w-3 shrink-0 text-ink-faint" aria-label="Duplicate" />
            ) : null}
          </div>
        ),
      }),
      helper.accessor('job_title', { header: FIELD_LABELS.job_title, cell: text }),
      helper.accessor('company', { header: FIELD_LABELS.company, cell: text }),
      helper.accessor('location', { header: FIELD_LABELS.location, cell: text }),
      helper.accessor('phone', { header: FIELD_LABELS.phone, cell: mono }),
      helper.accessor('email', { header: FIELD_LABELS.email, cell: mono }),
      helper.accessor('website', { header: FIELD_LABELS.website, cell: mono }),
      helper.accessor('overall_confidence', {
        header: 'Confidence',
        cell: (info) => (
          <div className="flex w-24 items-center gap-2">
            <span className="tnum text-[12px] text-ink">{Math.round(info.getValue() * 100)}</span>
            <ConfidenceBar value={info.getValue()} />
          </div>
        ),
      }),
      helper.accessor('status', {
        header: 'Status',
        cell: (info) => (
          <Badge tone={STATUS_TONE[info.getValue()]}>{info.getValue().replace('_', ' ')}</Badge>
        ),
        filterFn: 'equalsString',
      }),
      helper.accessor('filename', { header: 'File', cell: mono }),
    ],
    [],
  );

  const table = useReactTable({
    data,
    columns,
    state: { sorting, globalFilter, rowSelection, columnVisibility, columnFilters },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    onRowSelectionChange: setRowSelection,
    onColumnVisibilityChange: setColumnVisibility,
    onColumnFiltersChange: setColumnFilters,
    getRowId: (row) => row.card_id,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
  });

  const reviewFilter = columnFilters.find((filter) => filter.id === 'status');
  const selectedCount = Object.values(rowSelection).filter(Boolean).length;

  return (
    <motion.section layout className="mt-5">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <label className="relative flex min-w-48 flex-1 items-center sm:max-w-72">
          <Search className="pointer-events-none absolute left-3 h-3.5 w-3.5 text-ink-muted" aria-hidden />
          <span className="sr-only">Filter leads</span>
          <input
            value={globalFilter}
            onChange={(event) => setGlobalFilter(event.target.value)}
            placeholder="Filter leads…"
            className="h-9 w-full rounded-lg border border-line bg-surface/60 pl-9 pr-3 text-[13px] text-ink outline-none transition-colors placeholder:text-ink-faint focus:border-accent/60"
          />
        </label>

        <Button
          size="sm"
          variant={reviewFilter ? 'primary' : 'outline'}
          onClick={() =>
            setColumnFilters(reviewFilter ? [] : [{ id: 'status', value: 'needs_review' }])
          }
        >
          <TriangleAlert className="h-3.5 w-3.5" aria-hidden />
          Needs review
        </Button>

        <Popover
          label="Column visibility"
          trigger={({ toggle, open }) => (
            <Button size="sm" variant={open ? 'primary' : 'outline'} onClick={toggle}>
              <Columns3 className="h-3.5 w-3.5" aria-hidden />
              Columns
            </Button>
          )}
        >
          {() => (
            <div className="space-y-2.5">
              {table
                .getAllLeafColumns()
                .filter((column) => column.id !== 'select')
                .map((column) => (
                  <Checkbox
                    key={column.id}
                    checked={column.getIsVisible()}
                    onChange={(next) => column.toggleVisibility(next)}
                    label={<span className="capitalize">{column.id.replace('_', ' ')}</span>}
                  />
                ))}
            </div>
          )}
        </Popover>

        <div className="ml-auto flex items-center gap-3">
          <Checkbox
            checked={groupDuplicates}
            onChange={setGroupDuplicates}
            label={<span className="text-[12.5px]">Group duplicates</span>}
          />
          <span className="tnum text-[12px] text-ink-muted">
            {table.getFilteredRowModel().rows.length}/{leads.length}
            {selectedCount > 0 ? ` · ${selectedCount} selected` : ''}
          </span>
        </div>
      </div>

      {/* Desktop: a real table. Below md the same rows render as stacked cards. */}
      <div className="hidden overflow-hidden rounded-card border border-line bg-surface/50 md:block">
        <div className="max-h-[62vh] overflow-auto">
          <table className="w-full border-collapse text-left text-[13px]">
            <thead className="sticky top-0 z-10 bg-surface/95 backdrop-blur-sm">
              {table.getHeaderGroups().map((headerGroup) => (
                <tr key={headerGroup.id} className="border-b border-line">
                  {headerGroup.headers.map((header) => (
                    <th
                      key={header.id}
                      className="whitespace-nowrap px-3 py-2.5 text-[11px] font-medium uppercase tracking-[0.12em] text-ink-muted"
                    >
                      {header.isPlaceholder ? null : header.column.getCanSort() ? (
                        <button
                          type="button"
                          onClick={header.column.getToggleSortingHandler()}
                          className="flex items-center gap-1.5 transition-colors hover:text-ink"
                        >
                          {flexRender(header.column.columnDef.header, header.getContext())}
                          {header.column.getIsSorted() === 'asc' ? (
                            <ArrowUp className="h-3 w-3" aria-hidden />
                          ) : header.column.getIsSorted() === 'desc' ? (
                            <ArrowDown className="h-3 w-3" aria-hidden />
                          ) : null}
                        </button>
                      ) : (
                        flexRender(header.column.columnDef.header, header.getContext())
                      )}
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody>
              {table.getRowModel().rows.map((row) => (
                <tr
                  key={row.id}
                  onClick={() => onOpen(row.original.card_id)}
                  className={cx(
                    'cursor-pointer border-b border-line/60 transition-colors last:border-0 hover:bg-raised/60',
                    row.getIsSelected() && 'bg-accent/[0.06]',
                    row.original.duplicate_of && 'opacity-70',
                  )}
                >
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id} className="max-w-56 truncate px-3 py-2.5">
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <ul className="space-y-2.5 md:hidden">
        {table.getRowModel().rows.map((row) => {
          const lead = row.original;
          return (
            <li key={row.id}>
              <button
                type="button"
                onClick={() => onOpen(lead.card_id)}
                className="w-full rounded-card border border-line bg-surface/60 p-3.5 text-left"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-[14px] font-medium text-ink">
                      {[lead.first_name, lead.last_name].filter(Boolean).join(' ') || lead.filename}
                    </div>
                    <div className="truncate text-[12.5px] text-ink-muted">
                      {lead.job_title ?? '—'}
                      {lead.company ? ` · ${lead.company}` : ''}
                    </div>
                  </div>
                  <Badge tone={STATUS_TONE[lead.status]}>{lead.status.replace('_', ' ')}</Badge>
                </div>
                <div className="tnum mt-2 truncate text-[12px] text-ink-muted">
                  {lead.email ?? lead.phone ?? '—'}
                </div>
                <div className="mt-2.5 flex items-center gap-2">
                  <span className="tnum text-[11.5px] text-ink-muted">
                    {Math.round(lead.overall_confidence * 100)}%
                  </span>
                  <ConfidenceBar value={lead.overall_confidence} />
                </div>
              </button>
            </li>
          );
        })}
      </ul>
    </motion.section>
  );
}

function text({ getValue }: { getValue: () => string | null }) {
  return <span className="truncate text-ink-muted">{getValue() || '—'}</span>;
}

function mono({ getValue }: { getValue: () => string | null }) {
  return <span className="tnum truncate text-[12px] text-ink-muted">{getValue() || '—'}</span>;
}
