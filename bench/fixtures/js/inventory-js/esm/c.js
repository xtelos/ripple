import fallback, { other, g } from "./b.mjs";
import { dual } from "./dual.js";
import { dual as dualModule } from "./dual.mjs";

export function useAll() {
  return fallback() + other() + g() + dual() + dualModule();
}
