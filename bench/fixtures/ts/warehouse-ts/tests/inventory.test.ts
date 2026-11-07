import { reorderLevel } from '@wh/policy';
import { Crate, Inventory, Item, Perishable } from '../src';
import { formatQty } from '../src/util';

describe('Inventory', () => {
  const stock = (): Inventory =>
    new Inventory().addAll([Item.create('A1', 2.5), new Perishable('M1', 4, 3), new Crate('C1', 10, 12)]);

  it('lists labels and descriptions', () => {
    const inventory = stock();
    expect(inventory.labels()).toEqual(['A1:1', 'M1:1', 'C1:12']);
    expect(inventory.descriptions()).toEqual(['A1 x1', 'M1 x1 (3d)', 'C1 x12']);
  });

  it('finds low stock through a callback', () => {
    expect(stock().lowStock().map((item) => item.sku)).toEqual(['A1', 'M1']);
  });

  it('restocks every item', () => {
    const inventory = stock();
    inventory.restockAll(5);
    expect(inventory.labels()).toEqual(['A1:6', 'M1:6', 'C1:17']);
  });

  it('reads and writes accessors', () => {
    const item = Item.create('B1', 3);
    item.quantity = 7.9;
    expect(item.quantity).toBe(7);
    expect(item.total).toBe(21);
  });

  it('applies the default reorder level', () => {
    expect(reorderLevel('X9')).toBe(6);
    expect(formatQty(5)).toBe('    5');
  });
});
