import { fetchRate, quoteLater, shipCost } from '../src/shipping';

describe('shipping', () => {
  it('awaits the rate', async () => {
    expect(await shipCost('local', 2)).toBe(2);
  });

  it('chains with then', async () => {
    expect(await quoteLater('far', 2)).toBe(6);
  });

  it('fetches a rate directly', async () => {
    expect(await fetchRate('local')).toBe(1);
  });
});
