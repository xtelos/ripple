// Calls ripple cannot resolve from the source alone. Each of them runs, so the runtime
// trace sees the edge, but the ground truth lists them as unresolved: ripple must not guess.
import type { Inventory } from './inventory';

export interface Handler {
  handle(sku: string): string;
}

function pickSku(sku: string): string {
  return `pick ${sku}`;
}

function packSku(sku: string): string {
  return `pack ${sku}`;
}

export class PickHandler implements Handler {
  handle(sku: string): string {
    return pickSku(sku);
  }
}

export class PackHandler implements Handler {
  handle(sku: string): string {
    return packSku(sku);
  }
}

// The receiver is typed by an interface with two implementations.
export function run(handler: Handler, sku: string): string {
  return handler.handle(sku);
}

const TABLE: Record<string, (sku: string) => string> = { pick: pickSku, pack: packSku };

// The key is a variable, so the source does not say which entry runs.
export function dispatch(kind: string, sku: string): string {
  return TABLE[kind](sku);
}

// The method name is a variable.
export function callByName(inventory: Inventory, method: 'labels' | 'descriptions'): string[] {
  return inventory[method]();
}

function seal(sku: string): string {
  return `${sku}!`;
}

// A method decorator that swaps in a wrapper (decorators are deferred in ripple v1).
export function logged(_target: object, _key: string, descriptor: PropertyDescriptor): PropertyDescriptor {
  const original = descriptor.value as (...args: unknown[]) => unknown;
  descriptor.value = function (this: unknown, ...args: unknown[]) {
    return original.apply(this, args);
  };
  return descriptor;
}

export class Audited {
  @logged
  stamp(sku: string): string {
    return seal(sku);
  }
}
