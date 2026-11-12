#![allow(warnings)]
pub trait Make {
    const N: u32;
    fn make() -> Self;
    fn n() -> u32 { Self::N }
}
pub struct A;
impl Make for A {
    const N: u32 = 1;
    fn make() -> A { A }
}
pub struct B;
impl Make for B {
    const N: u32 = 2;
    fn make() -> B { B }
    fn n() -> u32 { 22 }
}
pub fn build<T: Make>() -> (T, u32) { (T::make(), T::n()) }
pub fn run() -> u32 {
    let (_a, n1) = build::<A>();
    let (_b, n2) = build::<B>();
    n1 + n2 + A::n() + <B as Make>::n()
}
