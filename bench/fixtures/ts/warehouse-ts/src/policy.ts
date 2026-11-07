// Reached through the tsconfig path alias `@wh/policy`.

function defaultLevel(): number {
  return 5;
}

function fallbackLevel(): number {
  return 3;
}

// A call made where the module is evaluated, so it belongs to the module itself.
export const DEFAULT_LEVEL = defaultLevel();

export function reorderLevel(sku: string, base: number = fallbackLevel()): number {
  return sku.startsWith('X') ? base * 2 : base;
}

export function levelFor(sku: string): number {
  return reorderLevel(sku, DEFAULT_LEVEL);
}
