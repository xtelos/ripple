import { Item } from './item';

export class Perishable extends Item {
  constructor(
    sku: string,
    unit: number,
    private readonly days: number,
  ) {
    super(sku, unit);
  }

  override describe(): string {
    return `${super.describe()} (${this.days}d)`;
  }
}

// No constructor in source: `new Crate(...)` runs the implicit one, which forwards to Item's.
export class Crate extends Item {}
