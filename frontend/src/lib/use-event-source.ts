'use client';

import { useEffect, useRef, useState } from 'react';
import { IS_MOCK } from './api';

export type ConnectionStatus = 'connecting' | 'open' | 'reconnecting' | 'polling' | 'closed';

export type SseHandlers = Record<string, (data: unknown) => void>;

interface EventSourceLike {
  addEventListener(type: string, listener: (e: MessageEvent) => void): void;
  close(): void;
  onerror: ((e: Event) => void) | null;
  onopen: ((e: Event) => void) | null;
}

interface Options {
  /** Set false once the job is terminal — the stream is closed and never re-opened. */
  enabled?: boolean;
  /** Called on an interval once the stream has failed `MAX_FAILURES` times. */
  poll?: () => void | Promise<void>;
  pollIntervalMs?: number;
}

const MAX_FAILURES = 3;
const POLL_INTERVAL_MS = 2500;

/**
 * One SSE hook for the whole app: exponential backoff on reconnect, and after three
 * consecutive failures it gives up on the stream and falls back to polling the job snapshot.
 * In mock mode the same code path drives a `MockEventSource`, so there is no mock-only branch
 * in any component.
 */
export function useEventSource(url: string | null, handlers: SseHandlers, opts: Options = {}): ConnectionStatus {
  const { enabled = true, poll, pollIntervalMs = POLL_INTERVAL_MS } = opts;
  const [status, setStatus] = useState<ConnectionStatus>('connecting');
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;
  const pollRef = useRef(poll);
  pollRef.current = poll;

  useEffect(() => {
    if (!url || !enabled) {
      setStatus('closed');
      return;
    }

    let cancelled = false;
    let source: EventSourceLike | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let poller: ReturnType<typeof setInterval> | null = null;
    let failures = 0;

    const startPolling = () => {
      setStatus('polling');
      if (poller !== null || !pollRef.current) return;
      void pollRef.current();
      poller = setInterval(() => void pollRef.current?.(), pollIntervalMs);
    };

    const connect = async () => {
      if (cancelled) return;
      setStatus(failures === 0 ? 'connecting' : 'reconnecting');

      if (IS_MOCK) {
        const jobId = url.split('/jobs/')[1]?.split('/')[0] ?? '';
        const { MockEventSource } = await import('./mock');
        if (cancelled) return;
        source = new MockEventSource(decodeURIComponent(jobId));
      } else {
        source = new EventSource(url);
      }

      const es = source;
      es.onopen = () => {
        failures = 0;
        setStatus('open');
      };
      for (const type of Object.keys(handlersRef.current)) {
        es.addEventListener(type, (e: MessageEvent) => {
          const fn = handlersRef.current[type];
          if (!fn) return;
          try {
            fn(JSON.parse(typeof e.data === 'string' ? e.data : '{}') as unknown);
          } catch {
            // A malformed frame must not kill the stream.
          }
        });
      }
      es.onerror = () => {
        es.close();
        if (cancelled || source !== es) return;
        source = null;
        failures += 1;
        if (failures >= MAX_FAILURES) {
          startPolling();
          return;
        }
        setStatus('reconnecting');
        const delay = Math.min(8000, 400 * 2 ** (failures - 1)) + Math.random() * 250;
        retry = setTimeout(() => void connect(), delay);
      };
    };

    void connect();

    return () => {
      cancelled = true;
      if (retry !== null) clearTimeout(retry);
      if (poller !== null) clearInterval(poller);
      source?.close();
      source = null;
    };
  }, [url, enabled, pollIntervalMs]);

  return status;
}
