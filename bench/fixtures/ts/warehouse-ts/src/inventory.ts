import { levelFor } from '@wh/policy';
import { Item } from './models';
import { label } from './util/index.ts';

export class Inventory {
  private readonly items = new Map<string, Item>();

  add(item: Item): this {
    this.items.set(item.sku, item);
    return this;
  }

  addAll(items: Item[]): this {
    items.forEach((item) => this.add(item));
    return this;
  }

  labels(): string[] {
    return [...this.items.values()].map((item) => label(item.sku, item.quantity));
  }

  descriptions(): string[] {
    return [...this.items.values()].map(describeItem);
  }

  lowStock(): Item[] {
    return this.select((item) => item.quantity < levelFor(item.sku));
  }

  restockAll(by: number): void {
    for (const item of this.items.values()) {
      item.restock(by);
    }
    // Only runs when a debugging flag is set, which the tests never do.
    if ((globalThis as { __WAREHOUSE_DEBUG__?: boolean }).__WAREHOUSE_DEBUG__) {
      this.trace(by);
    }
  }

  private select(keep: (item: Item) => boolean): Item[] {
    return [...this.items.values()].filter(keep);
  }

  private trace(by: number): void {
    console.log(`restocked by ${by}`, this.descriptions());
  }
}

function describeItem(item: Item): string {
  return item.describe();
}
