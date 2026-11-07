export function clamp(value: number, lo: number, hi: number): number {
  return Math.min(Math.max(value, lo), hi);
}

export const roundTo = (value: number, places = 2): number => {
  const factor = 10 ** places;
  return Math.round(value * factor) / factor;
};

export function percent(part: number, whole: number): number {
  return whole === 0 ? 0 : roundTo((part / whole) * 100, 1);
}
