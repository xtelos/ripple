import { act } from 'react';
import type { ReactElement } from 'react';
import { createRoot } from 'react-dom/client';
import { Item } from '../src';
import { Page } from '../src/ui/Page';
import { StockRow } from '../src/ui/Stock';

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const mount = (element: ReactElement): string => {
  const host = document.createElement('div');
  const root = createRoot(host);
  act(() => root.render(element));
  const html = host.innerHTML;
  act(() => root.unmount());
  return html;
};

describe('components', () => {
  it('renders a page with rows, a render prop and children', () => {
    const html = mount(<Page items={[Item.create('A1', 1), new Item('F1', 1, 20)]} />);
    expect(html).toContain('A1');
    expect(html).toContain('full');
  });

  it('renders the empty state', () => {
    expect(mount(<Page items={[]} />)).toContain('none');
  });

  it('renders a row on its own', () => {
    expect(mount(<StockRow item={Item.create('R1', 1)} />)).toContain('R1');
  });
});
