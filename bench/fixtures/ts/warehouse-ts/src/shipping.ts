async function tick(): Promise<void> {
  await Promise.resolve();
}

function finalise(cost: number): number {
  return Math.round(cost * 100) / 100;
}

export async function fetchRate(zone: string): Promise<number> {
  await tick();
  return zone === 'local' ? 1 : 3;
}

export async function shipCost(zone: string, kg: number): Promise<number> {
  const rate = await fetchRate(zone);
  return finalise(rate * kg);
}

export function quoteLater(zone: string, kg: number): Promise<number> {
  return fetchRate(zone).then((rate) => finalise(rate * kg));
}
