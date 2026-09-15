import { NextResponse, type NextRequest } from 'next/server';
import * as mock from '@/lib/mock/engine';
import { buildCsv, buildXlsx } from '@/lib/mock/xlsx';

// The mock has to stream, so it can never be statically rendered.
export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

const ENABLED = process.env.NEXT_PUBLIC_MOCK === '1';

type Params = { params: Promise<{ path?: string[] }> };

function fail(status: number, detail: string, code: string): NextResponse {
  return NextResponse.json({ detail, code }, { status });
}

const NOT_FOUND = (): NextResponse => fail(404, 'Unknown mock endpoint.', 'NOT_FOUND');

function guard(): NextResponse | null {
  return ENABLED ? null : fail(404, 'Mock API is disabled. Set NEXT_PUBLIC_MOCK=1.', 'MOCK_DISABLED');
}

export async function GET(request: NextRequest, { params }: Params): Promise<Response> {
  const blocked = guard();
  if (blocked) return blocked;
  const path = (await params).path ?? [];

  if (path[0] === 'health' && path.length === 1) {
    return NextResponse.json(mock.health());
  }

  if (path[0] === 'cards' && path[2] === 'image' && path[1]) {
    const image = mock.getImage(path[1]);
    if (!image) return fail(404, 'Card image not found.', 'CARD_NOT_FOUND');
    return new Response(new Blob([image.bytes as BlobPart], { type: image.mime }), {
      headers: { 'content-type': image.mime, 'cache-control': 'public, max-age=3600' },
    });
  }

  if (path[0] === 'batches' && path[1]) {
    const batchId = path[1];

    if (path.length === 2) {
      const batch = mock.getBatch(batchId);
      return batch ? NextResponse.json(batch) : fail(404, 'Unknown batch id.', 'BATCH_NOT_FOUND');
    }

    if (path[2] === 'events') return streamEvents(request, batchId);

    if (path[2] === 'export.xlsx' || path[2] === 'export.csv') {
      const search = request.nextUrl.searchParams;
      const rows = mock.exportRows(batchId, {
        includeLowConfidence: search.get('include_low_confidence') !== 'false',
        includeDuplicates: search.get('include_duplicates') !== 'false',
      });
      if (!rows) return fail(404, 'Unknown batch id.', 'BATCH_NOT_FOUND');
      const stem = `leadforge-${batchId.slice(0, 8)}`;

      if (path[2] === 'export.csv') {
        return new Response(buildCsv(rows), {
          headers: {
            'content-type': 'text/csv; charset=utf-8',
            'content-disposition': `attachment; filename="${stem}.csv"`,
          },
        });
      }
      const xlsx = buildXlsx(rows);
      return new Response(
        new Blob([xlsx as BlobPart], {
          type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        }),
        {
          headers: {
            'content-type':
              'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'content-disposition': `attachment; filename="${stem}.xlsx"`,
          },
        },
      );
    }
  }

  return NOT_FOUND();
}

export async function POST(request: NextRequest, { params }: Params): Promise<Response> {
  const blocked = guard();
  if (blocked) return blocked;
  const path = (await params).path ?? [];

  if (path[0] === 'vlm' && path[1] === 'warmup') {
    return NextResponse.json(mock.warmup());
  }

  if (path[0] === 'batches' && path.length === 1) {
    const form = await request.formData();
    const uploads: mock.Upload[] = [];
    for (const entry of form.getAll('files')) {
      if (typeof entry === 'string') continue;
      uploads.push({
        name: entry.name,
        type: entry.type,
        bytes: new Uint8Array(await entry.arrayBuffer()),
      });
    }
    try {
      return NextResponse.json(mock.createBatch(uploads), { status: 201 });
    } catch (error) {
      if (error instanceof mock.MockError) return fail(error.status, error.message, error.code);
      throw error;
    }
  }

  if (path[0] === 'batches' && path[2] === 'retry' && path[1]) {
    const body = (await request.json().catch(() => ({}))) as { card_ids?: string[] };
    const batch = mock.retry(path[1], body.card_ids);
    return batch ? NextResponse.json(batch) : fail(404, 'Unknown batch id.', 'BATCH_NOT_FOUND');
  }

  return NOT_FOUND();
}

export async function PATCH(request: NextRequest, { params }: Params): Promise<Response> {
  const blocked = guard();
  if (blocked) return blocked;
  const path = (await params).path ?? [];

  if (path[0] === 'leads' && path[1] && path.length === 2) {
    const body = (await request.json().catch(() => null)) as Record<string, unknown> | null;
    if (!body) return fail(422, 'Body must be a JSON object of fields to update.', 'INVALID_BODY');
    const lead = mock.patchLead(path[1], body);
    return lead ? NextResponse.json(lead) : fail(404, 'Unknown card id.', 'CARD_NOT_FOUND');
  }

  return NOT_FOUND();
}

function streamEvents(request: NextRequest, batchId: string): Response {
  if (!mock.getBatch(batchId)) return fail(404, 'Unknown batch id.', 'BATCH_NOT_FOUND');

  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      let unsubscribe: (() => void) | null = null;
      let ping: ReturnType<typeof setInterval> | null = null;
      let closed = false;

      const cleanup = (): void => {
        if (closed) return;
        closed = true;
        if (ping) clearInterval(ping);
        unsubscribe?.();
      };

      const send = (event: string, data: unknown): void => {
        if (closed) return;
        controller.enqueue(encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
      };

      unsubscribe = mock.subscribe(batchId, (event, data) => {
        send(event, data);
        if (event === 'batch.completed') {
          cleanup();
          controller.close();
        }
      });
      // A batch that was already finished closes during subscribe(), before the handle exists.
      if (closed) unsubscribe?.();

      if (!closed) {
        ping = setInterval(() => send('ping', {}), 15_000);
        request.signal.addEventListener('abort', () => {
          cleanup();
          try {
            controller.close();
          } catch {
            // Already closed by the batch.completed path.
          }
        });
      }
    },
  });

  return new Response(stream, {
    headers: {
      'content-type': 'text/event-stream; charset=utf-8',
      'cache-control': 'no-cache, no-transform',
      connection: 'keep-alive',
      'x-accel-buffering': 'no',
    },
  });
}
