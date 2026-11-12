#![allow(warnings)]
pub trait D { fn d() -> u32 { 1000 } fn e() -> u32 { 5 } }
pub struct X;
macro_rules! gen_d { () => { fn d() -> u32 { 2000 } } }
impl D for X { gen_d!(); }
pub struct Y;
impl D for Y {}
pub fn x_d() -> u32 { X::d() }
pub fn x_q() -> u32 { <X as D>::d() }
pub fn x_e() -> u32 { <X as D>::e() }
pub fn y_d() -> u32 { Y::d() }
pub fn y_q() -> u32 { <Y as D>::d() }
#[cfg(test)]
mod t {
    use super::*;
    #[test]
    fn which() { assert_eq!((x_d(), x_q(), x_e(), y_d(), y_q()), (2000, 2000, 5, 1000, 1000)); }
}
