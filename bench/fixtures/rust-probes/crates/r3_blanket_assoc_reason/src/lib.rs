#![allow(warnings)]
pub trait Tr { fn f() -> u32; }
pub struct S;
impl Tr for S { fn f() -> u32 { 1 } }
impl<T: Copy> Tr for T { fn f() -> u32 { 2 } }
pub fn a() -> u32 { S::f() }
pub fn b() -> u32 { u8::f() }
#[cfg(test)]
mod t { #[test] fn which() { assert_eq!((super::a(), super::b()), (1, 2)); } }
