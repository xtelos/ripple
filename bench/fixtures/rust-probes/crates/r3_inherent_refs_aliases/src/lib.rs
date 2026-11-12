#![allow(warnings)]
pub trait T { fn f() -> u32; }
pub struct S;
impl S { pub fn f() -> u32 { 1 } }
impl T for &S { fn f() -> u32 { 2 } }
type A = S;
type R = &'static S;
pub fn a() -> u32 { A::f() }
pub fn r() -> u32 { <R as T>::f() }
pub fn rr() -> u32 { <&S as T>::f() }
pub fn s() -> u32 { S::f() }
#[cfg(test)]
mod t { #[test] fn which() { assert_eq!((super::a(), super::r(), super::rr(), super::s()), (1, 2, 2, 1)); } }
