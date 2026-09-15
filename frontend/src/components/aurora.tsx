/**
 * The aurora field: one fixed layer of drifting gradients behind the whole app. All
 * motion is CSS transforms on four elements, so it costs nothing per frame in JS.
 * Hue and intensity follow `data-phase` on <html> (see use-phase.ts).
 */
export function Aurora() {
  return (
    <div className="aurora" aria-hidden>
      <div className="aurora-blob aurora-a" />
      <div className="aurora-blob aurora-b" />
      <div className="aurora-blob aurora-c" />
      <div className="aurora-sweep" />
      <div className="aurora-vignette" />
      <div className="aurora-grain" />
    </div>
  );
}
