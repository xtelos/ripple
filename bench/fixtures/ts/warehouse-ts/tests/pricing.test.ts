import { Item, pricing } from '../src';
import { percent } from '../src/util/math';

describe('pricing', () => {
  it('prices a line with the default discount', () => {
    expect(pricing.lineTotal(Item.create('A', 10))).toBe(9);
  });

  it('prices an order with an explicit discount', () => {
    const items = [Item.create('A', 10), new Item('B', 5, 4)];
    expect(pricing.orderTotal(items, 0.5)).toBe(15);
  });

  it('prices an order with no discount given', () => {
    const items = [Item.create('A', 10), new Item('B', 5, 4)];
    expect(pricing.orderTotal(items)).toBe(27);
  });

  it('computes percentages', () => {
    expect(percent(1, 4)).toBe(25);
    expect(percent(1, 0)).toBe(0);
  });
});
