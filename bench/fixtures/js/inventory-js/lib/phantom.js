import { helper } from "./app.js";

// In JavaScript this is two comparisons, (helper < a) > (b): helper is never called.
export function phantom(a, b) {
  const result = helper < a > (b);
  return result;
}
