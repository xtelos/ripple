#![allow(warnings)]
macro_rules! unit { ($n:ident) => { pub struct $n; } }
unit!(M);
pub trait T { fn f() -> u32; }
pub struct S;
impl S {
    pub fn make() -> u32 { 1 }
    pub fn go() -> u32 { Self::make() }
}
impl T for S { fn f() -> u32 { 2 } }
pub fn a() -> u32 { S::make() }
pub fn b() -> u32 { <S as T>::f() }
pub fn c() -> u32 { S::go() }
pub fn d() -> u32 { S::f() }
#[cfg(test)]
mod t { #[test] fn which() { assert_eq!((super::a(), super::b(), super::c(), super::d()), (1, 2, 1, 2)); } }
