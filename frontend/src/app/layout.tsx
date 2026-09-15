import type { Metadata, Viewport } from 'next';
import { GeistMono } from 'geist/font/mono';
import { GeistSans } from 'geist/font/sans';
import { Toaster } from 'sonner';
import { Aurora } from '@/components/aurora';
import { Providers } from '@/components/providers';
import './globals.css';

export const metadata: Metadata = {
  title: 'LeadForge — Business cards in. Pipeline-ready leads out.',
  description:
    'Bulk business-card extraction. Drop up to 25 cards and a self-hosted Qwen2.5-VL vision model returns structured, confidence-scored leads ready for your CRM.',
};

export const viewport: Viewport = {
  themeColor: '#0b0c10',
  colorScheme: 'dark',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-phase="idle" className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body className="min-h-dvh antialiased">
        <Aurora />
        <Providers>{children}</Providers>
        <Toaster
          theme="dark"
          position="bottom-right"
          toastOptions={{
            style: {
              background: 'color-mix(in oklch, var(--color-surface) 92%, transparent)',
              border: '1px solid var(--color-line)',
              color: 'var(--color-ink)',
              borderRadius: '12px',
            },
          }}
        />
      </body>
    </html>
  );
}
