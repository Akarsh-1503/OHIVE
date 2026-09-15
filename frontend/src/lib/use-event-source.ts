'use client';

import { useEffect, useRef, useState } from 'react';

export type ConnectionState = 'connecting' | 'live' | 'reconnecting' | 'polling' | 'closed';

export type SseHandlers = Record<string, (data: unknown) => void>;

interface Options {
  /** When false the stream is torn down (batch finished, or unknown batch). */
  enabled: boolean;
  /**
   * Called every POLL_INTERVAL_MS once the stream has failed MAX_RETRIES times.
   * Return true when the batch is finished so polling can stop.
   */
  onPoll?: () => Promise<boolean>;
}

const MAX_RETRIES = 3;
const POLL_INTERVAL_MS = 2500;
const MAX_BACKOFF_MS = 15_000;

/**
 * The single place EventSource is touched. Reconnects with exponential backoff and jitter,
 * then degrades to polling rather than leaving the UI frozen. Handlers are read through a
 * ref so re-rendering the consumer never re-opens the stream.
 */
export function useEventSource(
  url: string | null,
  handlers: SseHandlers,
  options: Options,
): { state: ConnectionState; retries: number } {
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;
  const pollRef = useRef(options.onPoll);
  pollRef.current = options.onPoll;

  const [state, setState] = useState<ConnectionState>('connecting');
  const [retries, setRetries] = useState(0);

  const enabled = options.enabled;

  useEffect(() => {
    if (!url || !enabled) {
      setState('closed');
      return;
    }

    let source: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    let pollTimer: ReturnType<typeof setInterval> | undefined;
    let failures = 0;
    let disposed = false;

    const startPolling = (): void => {
      if (pollTimer !== undefined) return;
      setState('polling');
      const tick = async (): Promise<void> => {
        const finished = await pollRef.current?.().catch(() => false);
        if (finished && pollTimer !== undefined) {
          clearInterval(pollTimer);
          pollTimer = undefined;
          setState('closed');
        }
      };
      void tick();
      pollTimer = setInterval(() => void tick(), POLL_INTERVAL_MS);
    };

    const connect = (): void => {
      if (disposed) return;
      const es = new EventSource(url);
      source = es;

      es.onopen = () => {
        failures = 0;
        setRetries(0);
        setState('live');
      };

      for (const name of Object.keys(handlersRef.current)) {
        es.addEventListener(name, (event) => {
          const message = event as MessageEvent<string>;
          let parsed: unknown = null;
          try {
            parsed = message.data ? (JSON.parse(message.data) as unknown) : null;
          } catch {
            return; // A truncated frame during a proxy hiccup is not worth tearing down for.
          }
          handlersRef.current[name]?.(parsed);
        });
      }

      es.onerror = () => {
        es.close();
        if (disposed) return;
        failures += 1;
        setRetries(failures);
        if (failures >= MAX_RETRIES) {
          startPolling();
          return;
        }
        setState('reconnecting');
        const backoff = Math.min(1000 * 2 ** (failures - 1), MAX_BACKOFF_MS);
        reconnectTimer = setTimeout(connect, backoff + Math.random() * 400);
      };
    };

    setState('connecting');
    connect();

    return () => {
      disposed = true;
      source?.close();
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (pollTimer !== undefined) clearInterval(pollTimer);
    };
  }, [url, enabled]);

  return { state, retries };
}
