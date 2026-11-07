import { clamp } from './math';

export const padLeft = (text: string, width: number): string => text.padStart(width, ' ');

export function formatQty(qty: number): string {
  return padLeft(String(clamp(qty, 0, 9999)), 5);
}

export default function label(name: string, qty: number): string {
  return `${name}:${formatQty(qty).trim()}`;
}
