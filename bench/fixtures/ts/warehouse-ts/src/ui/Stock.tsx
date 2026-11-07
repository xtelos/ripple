import { useMemo } from 'react';
import { Item } from '../models';
import { percent } from '../util/math';
import { Badge } from './Panel';

// A hook: called by a component, and itself calling a helper.
export function useFill(qty: number, capacity: number) {
  const pct = useMemo(() => percent(qty, capacity), [qty, capacity]);
  return { pct, full: pct >= 100 };
}

function FullMark() {
  return <b>full</b>;
}

export function StockRow({ item }: { item: Item }) {
  const fill = useFill(item.quantity, 20);
  return (
    <li>
      {item.sku}
      <Badge text={`${fill.pct}%`} />
      {fill.full && <FullMark />}
    </li>
  );
}

export function StockList({ items }: { items: Item[] }) {
  return (
    <ul>
      {items.map((item) => (
        <StockRow key={item.sku} item={item} />
      ))}
    </ul>
  );
}
