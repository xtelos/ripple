#![allow(warnings)]
pub struct S;
pub trait Conv<A> { fn conv(a: A) -> u32; }
impl Conv<u8> for S { fn conv(_: u8) -> u32 { 1 } }
macro_rules! more { () => { impl Conv<u16> for S { fn conv(_: u16) -> u32 { 2 } } } }
more!();
pub fn a() -> u32 { S::conv(5u16) }
pub fn b() -> u32 { S::conv(5u8) }
#[cfg(test)]
mod t { #[test] fn which() { assert_eq!((super::a(), super::b()), (2, 1)); } }
