#![allow(warnings)]
#[macro_use]
mod macros {
    macro_rules! gen_inherent { ($t:ident) => { impl $t { pub fn f() -> u32 { 2 } } } }
}
#[macro_export]
macro_rules! gen_two { ($t:ident) => { impl $t { pub fn g() -> u32 { 4 } } } }
pub trait T { fn f() -> u32; fn g() -> u32; }
pub struct A;
pub struct B;
impl T for A { fn f() -> u32 { 1 } fn g() -> u32 { 3 } }
impl T for B { fn f() -> u32 { 1 } fn g() -> u32 { 3 } }
gen_inherent!(A);
crate::gen_two!(B);
pub fn a_f() -> u32 { A::f() }
pub fn a_g() -> u32 { A::g() }
pub fn b_f() -> u32 { B::f() }
pub fn b_g() -> u32 { B::g() }
#[cfg(test)]
mod t { #[test] fn which() { assert_eq!((super::a_f(), super::a_g(), super::b_f(), super::b_g()), (2, 3, 1, 4)); } }
