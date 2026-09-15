'use client';

import { useEffect } from 'react';

export type AuroraPhase = 'idle' | 'processing' | 'done' | 'error';

/**
 * Writes the app phase onto <html> so the background can react without the aurora
 * subscribing to React state (it would re-render behind every SSE frame otherwise).
 */
export function useAuroraPhase(phase: AuroraPhase): void {
  useEffect(() => {
    document.documentElement.dataset.phase = phase;
    return () => {
      document.documentElement.dataset.phase = 'idle';
    };
  }, [phase]);
}
