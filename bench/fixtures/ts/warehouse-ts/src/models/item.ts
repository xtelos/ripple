import { clamp, roundTo } from '../util';

function startingQty(): number {
  return 1;
}

function normalise(qty: number): number {
  return clamp(Math.trunc(qty), 0, 9999);
}

export class Item {
  constructor(
    public readonly sku: string,
    protected unit: number,
    private qty: number = startingQty(),
  ) {}

  get total(): number {
    return roundTo(this.unit * this.qty);
  }

  get quantity(): number {
    return this.qty;
  }

  set quantity(next: number) {
    this.qty = normalise(next);
  }

  describe(): string {
    return `${this.sku} x${this.qty}`;
  }

  restock(by: number): string {
    this.quantity = this.qty + by;
    return this.describe();
  }

  static create(sku: string, unit: number): Item {
    return new Item(sku, unit);
  }
}
