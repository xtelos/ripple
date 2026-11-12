import * as m0 from "./lib";
export function call0() { m0.f(); }
import * as m1 from "./lib/index.mjs";
export function call1() { m1.f(); }
import * as m2 from "./cjs";
export function call2() { m2.f(); }
import * as m3 from "./cjs/index.cjs";
export function call3() { m3.f(); }
