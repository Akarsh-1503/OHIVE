'use client';

import { MotionConfig } from 'framer-motion';
import { Toaster } from 'sonner';
import type { ReactNode } from 'react';

export function Providers({ children }: { children: ReactNode }) {
  return (
    <MotionConfig reducedMotion="user">
      {children}
      <Toaster
        theme="dark"
        position="top-right"
        toastOptions={{
          style: {
            background: 'var(--color-surface)',
            border: '1px solid var(--color-line)',
            color: 'var(--color-ink)',
            borderRadius: '12px',
          },
        }}
      />
    </MotionConfig>
  );
}
