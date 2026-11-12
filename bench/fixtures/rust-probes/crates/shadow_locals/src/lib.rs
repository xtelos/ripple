#![allow(warnings)]
fn f() -> u32 { 1 }
fn g() -> u32 { 2 }
fn h() -> u32 { 3 }
fn k() -> u32 { 4 }
fn m() -> u32 { 5 }
pub fn let_closure() -> u32 {
    let f = || 10;
    f()
}
pub fn param(g: fn() -> u32) -> u32 { g() }
pub fn closure_param() -> u32 {
    let apply = |h: fn() -> u32| h();
    apply(k)
}
pub fn if_let() -> u32 {
    let o: Option<fn() -> u32> = Some(k);
    if let Some(k) = o { k() } else { 0 }
}
pub fn match_arm(x: Option<fn() -> u32>) -> u32 {
    match x {
        Some(m) => m(),
        None => m(),
    }
}
pub fn loop_pattern(fs: Vec<fn() -> u32>) -> u32 {
    let mut n = 0;
    for h in fs { n += h(); }
    n + h()
}
pub fn after_block() -> u32 {
    { let f = || 20; f(); }
    f()
}
