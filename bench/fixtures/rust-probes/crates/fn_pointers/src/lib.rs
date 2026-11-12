#![allow(warnings)]
fn g() -> u32 { 1 }
const F: fn() -> u32 = g;
static FNS: [fn() -> u32; 1] = [g];
pub struct H {
    pub cb: fn() -> u32,
}
pub fn run(h: &H) -> u32 {
    F() + FNS[0]() + (h.cb)() + (g)() + g()
}
