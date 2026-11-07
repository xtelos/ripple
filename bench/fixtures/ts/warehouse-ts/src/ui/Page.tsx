import { Item } from '../models';
import { Badge, DataList, Panel } from './Panel';
import { StockList } from './Stock';

function Empty() {
  return <p>none</p>;
}

export function Page({ items }: { items: Item[] }) {
  return (
    <Panel title="Stock">
      <StockList items={items} />
      <DataList rows={items} render={(item) => <Badge text={item.sku} />} />
      {items.length === 0 && <Empty />}
    </Panel>
  );
}
