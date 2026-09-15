import Link from 'next/link';

export function Wordmark({ href = '/' }: { href?: string }) {
  return (
    <Link href={href} className="group flex items-center gap-2.5" aria-label="LeadForge home">
      <span className="relative flex h-8 w-8 items-center justify-center rounded-[10px] bg-[linear-gradient(135deg,var(--color-accent),var(--color-accent-2))] shadow-[0_4px_20px_-6px_color-mix(in_oklch,var(--color-accent)_70%,transparent)]">
        <svg viewBox="0 0 24 24" className="h-4 w-4" aria-hidden fill="none">
          <rect x="3.5" y="6" width="13" height="9" rx="2" stroke="var(--color-void)" strokeWidth="1.8" />
          <path d="M8 18.5h11a2 2 0 0 0 2-2V9.5" stroke="var(--color-void)" strokeWidth="1.8" strokeLinecap="round" />
          <path d="M6.5 9.5h4M6.5 12h7" stroke="var(--color-void)" strokeWidth="1.8" strokeLinecap="round" />
        </svg>
      </span>
      <span className="text-[15px] font-semibold tracking-tight text-ink">
        Lead<span className="text-ink-muted transition-colors group-hover:text-ink">Forge</span>
      </span>
    </Link>
  );
}
