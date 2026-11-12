#![allow(warnings)]
mod x {
    pub fn f() -> u32 { 1 }
    pub fn late() -> u32 { 2 }
}
fn f() -> u32 { 100 }
pub fn block_use() -> u32 {
    use crate::x::f;
    f()
}
pub fn nested_fn() -> u32 {
    fn f() -> u32 { 200 }
    f()
}
pub fn later_item() -> u32 {
    let a = helper();
    fn helper() -> u32 { 3 }
    a
}
pub fn inner_blocks() -> u32 {
    let a = { use crate::x::f; f() };
    let b = f();
    let c = { fn f() -> u32 { 300 } f() };
    a + b + c
}
pub fn use_after_use() -> u32 {
    use crate::x::f;
    let a = f();
    use crate::x::late as f2;
    a + f2()
}
pub fn closure_with_block() -> u32 {
    let c = || { use crate::x::late; late() };
    c()
}
pub fn outer() -> u32 {
    fn inner() -> u32 { deeper() }
    fn deeper() -> u32 { f() }
    inner()
}
fn declared_after_use() -> u32 { after() }
fn after() -> u32 { 4 }
#[cfg(test)]
mod proof {
    #[test]
    fn which_runs() {
        assert_eq!(super::block_use(), 1);
        assert_eq!(super::nested_fn(), 200);
        assert_eq!(super::inner_blocks(), 1 + 100 + 300);
        assert_eq!(super::outer(), 100);
    }
}
