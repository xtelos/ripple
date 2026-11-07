import { Inventory, Item } from '../src';
import { Audited, PackHandler, PickHandler, callByName, dispatch, run } from '../src/dynamic';

describe('dynamic calls', () => {
  it('calls through an interface-typed value', () => {
    expect(run(new PickHandler(), 'A1')).toBe('pick A1');
    expect(run(new PackHandler(), 'A1')).toBe('pack A1');
  });

  it('calls through a table indexed by a variable', () => {
    expect(dispatch('pick', 'B')).toBe('pick B');
    expect(dispatch('pack', 'B')).toBe('pack B');
  });

  it('calls a method chosen by name', () => {
    const inventory = new Inventory().add(Item.create('A', 1));
    expect(callByName(inventory, 'labels')).toEqual(['A:1']);
    expect(callByName(inventory, 'descriptions')).toEqual(['A x1']);
  });

  it('calls a decorated method', () => {
    expect(new Audited().stamp('A')).toBe('A!');
  });
});
