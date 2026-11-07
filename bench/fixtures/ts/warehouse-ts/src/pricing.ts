import { Item } from './models';
import * as math from './util/math';

export function defaultDiscount(): number {
  return 0.1;
}

// `discount` has no value when the caller passes none, so its default runs in lineTotal.
export function lineTotal(item: Item, discount = defaultDiscount()): number {
  return math.roundTo(item.total * (1 - math.clamp(discount, 0, 1)));
}

export const orderTotal = (items: Item[], discount?: number): number =>
  items.reduce((sum, item) => sum + lineTotal(item, discount), 0);
