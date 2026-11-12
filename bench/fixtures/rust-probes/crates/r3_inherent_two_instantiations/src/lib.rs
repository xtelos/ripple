#![allow(warnings)]
pub struct G<Z>(Z);
impl G<u8> { pub fn f() -> u32 { 1 } }
impl G<u16> { pub fn f() -> u32 { 2 } }
pub fn a() -> u32 { G::<u8>::f() }
pub fn b() -> u32 { G::<u16>::f() }
pub fn c() -> u32 { <G<u16>>::f() }
pub fn d() -> u32 { let _g: G<u8> = G(0); <G<u8>>::f() }
#[cfg(test)]
mod t { #[test] fn which() { assert_eq!((super::a(), super::b(), super::c(), super::d()), (1, 2, 2, 1)); } }
