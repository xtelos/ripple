export * from "./a.mjs";
export { f as g } from "./a.mjs";

export default function fallback() {
  return 0;
}
