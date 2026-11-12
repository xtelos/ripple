import { helper } from "./app.js";

export function viaOptional(box, maybe) {
  box?.method();
  maybe?.();
  helper?.();
  return box?.value;
}
