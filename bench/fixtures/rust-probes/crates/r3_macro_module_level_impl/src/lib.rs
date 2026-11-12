#![allow(warnings)]
pub trait T { fn f() -> u32; }
macro_rules! make { ($t:ty, $n:expr) => { impl $t { pub fn f() -> u32 { $n } } } }
pub struct U;
impl T for U { fn f() -> u32 { 10 } }
make!(U, 20);
pub mod other {
    pub fn run() -> u32 { crate::U::f() }
    pub fn run_q() -> u32 { <crate::U as crate::T>::f() }
}
#[cfg(test)]
mod t { #[test] fn which() { assert_eq!(super::other::run(), 20); assert_eq!(super::other::run_q(), 10); } }
