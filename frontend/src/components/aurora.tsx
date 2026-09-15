export type AuroraState = 'idle' | 'processing' | 'done' | 'failed';

/** One fixed element, pure CSS. The hue and pace track the pipeline state. */
export function Aurora({ state = 'idle' }: { state?: AuroraState }) {
  return <div className="aurora" data-state={state} aria-hidden />;
}
