#![allow(warnings)]
pub enum E {
    A(u32),
    B { x: u32 },
    Cc(u32),
}
pub struct W(pub u32);
pub struct U;
#[allow(non_snake_case)]
fn Cc() -> u32 { 7 }
fn helper() -> u32 { 1 }
use E::A;
pub fn run() -> u32 {
    let a = A(1);
    let b = E::A(2);
    let w = W(3);
    let o = Some(4);
    let r: Result<u32, ()> = Ok(5);
    let v: Vec<E> = vec![1, 2].into_iter().map(E::A).collect();
    let _ = (a, b, w, o, r, U, E::B { x: 1 }, v);
    helper() + Cc()
}
pub fn block_glob() -> E {
    use E::*;
    // the block's glob brings the variant Cc, which shadows the module-level fn
    Cc(1)
}
