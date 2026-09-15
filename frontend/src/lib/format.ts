export function ms(value: number): string {
  if (value < 1000) return `${Math.round(value)} ms`;
  return `${(value / 1000).toFixed(value < 10000 ? 2 : 1)} s`;
}

export function secs(value: number): string {
  return `${value.toFixed(value < 10 ? 2 : 1)} s`;
}

export function int(value: number): string {
  return Math.round(value).toLocaleString('en-US');
}

export function fixed(value: number, digits = 2): string {
  return value.toFixed(digits);
}

export function megabytes(bytes: number): string {
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function clockFromSeconds(t: number): string {
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  return `${String(m).padStart(2, '0')}:${s.toFixed(2).padStart(5, '0')}`;
}

export function pct(value: number): string {
  return `${value.toFixed(1)}%`;
}
