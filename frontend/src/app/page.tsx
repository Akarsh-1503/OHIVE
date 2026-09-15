import { Gauge, Radio, Sheet } from 'lucide-react';
import { Dropzone } from '@/components/dropzone';
import { HealthPill } from '@/components/health-pill';
import { PipelineRail } from '@/components/pipeline-rail';
import { Wordmark } from '@/components/wordmark';

const FEATURES = [
  {
    Icon: Gauge,
    title: 'Confidence on every field',
    body: 'Each field returns its own score. Anything the model is unsure about is flagged for review instead of landing silently in your CRM.',
  },
  {
    Icon: Radio,
    title: 'Streamed, not batched',
    body: 'Results arrive card by card over a live event stream, so you can start reviewing the first lead while the last one is still in the model.',
  },
  {
    Icon: Sheet,
    title: 'Excel on the way out',
    body: 'Export a styled .xlsx or CSV in one click, with duplicates grouped and low-confidence rows optional.',
  },
];

export default function HomePage() {
  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-6xl flex-col px-5 pb-24 sm:px-8">
      <header className="flex flex-col gap-4 py-5 sm:flex-row sm:items-center sm:justify-between">
        <Wordmark />
        <HealthPill />
      </header>

      <section className="pt-6 sm:pt-10">
        <span className="inline-flex items-center gap-2 rounded-full border border-line bg-surface/60 px-3 py-1 text-[12px] text-ink-muted">
          <span className="h-1.5 w-1.5 rounded-full bg-[linear-gradient(120deg,var(--color-accent),var(--color-accent-2))]" />
          Self-hosted Qwen2.5-VL · vision-language extraction
        </span>

        <h1 className="mt-5 max-w-3xl text-[clamp(2.2rem,5.6vw,3.6rem)] font-semibold leading-[1.04] tracking-[-0.035em] text-ink">
          Business cards in.
          <br />
          <span className="bg-[linear-gradient(100deg,var(--color-accent),var(--color-accent-2))] bg-clip-text text-transparent">
            Pipeline-ready leads out.
          </span>
        </h1>

        <p className="mt-4 max-w-xl text-[15px] leading-6 text-ink-muted sm:mt-5 sm:text-[16.5px] sm:leading-7">
          Drop a stack of cards and a self-hosted Qwen2.5-VL vision model reads every one,
          returning structured, confidence-scored leads you can check against the original and
          export straight to Excel.
        </p>

        <PipelineRail active={0} muted className="mt-7 max-w-lg sm:mt-8" />
      </section>

      <section className="mt-7 sm:mt-9">
        <Dropzone />
      </section>

      <section className="mt-20 grid gap-px overflow-hidden rounded-2xl border border-line bg-line/60 sm:grid-cols-3">
        {FEATURES.map(({ Icon, title, body }) => (
          <article key={title} className="bg-surface/60 p-6">
            <Icon className="h-4 w-4 text-accent" aria-hidden />
            <h2 className="mt-4 text-[15px] font-medium tracking-tight text-ink">{title}</h2>
            <p className="mt-2 text-[13.5px] leading-6 text-ink-muted">{body}</p>
          </article>
        ))}
      </section>

      <footer className="mt-16 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-6 text-[12.5px] text-ink-muted">
        <span>LeadForge · bulk card extraction</span>
        <span className="tnum">
          Max 25 cards per batch · 12 MB each · JPEG, PNG, WebP, HEIC, PDF
        </span>
      </footer>
    </main>
  );
}
