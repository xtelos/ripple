#![allow(warnings)]
pub trait Tr { fn f() -> u32; }
pub trait Conv<A, B> { fn conv() -> u32; }
impl Tr for [u8; 4] { fn f() -> u32 { 1 } }
impl Tr for (u8, bool) { fn f() -> u32 { 2 } }
impl Tr for fn(u8) -> u8 { fn f() -> u32 { 3 } }
pub struct S;
impl Conv<u8, Vec<u16>> for S { fn conv() -> u32 { 4 } }
impl Conv<u16, Option<(u8, u8)>> for S { fn conv() -> u32 { 5 } }
pub fn a() -> u32 { <[u8; 4] as Tr>::f() + <(u8, bool) as Tr>::f() }
pub fn b() -> u32 { <S as Conv<u8, Vec<u16>>>::conv() + <S as Conv<u16, Option<(u8, u8)>>>::conv() }
#[cfg(test)]
mod t { #[test] fn which() { assert_eq!((super::a(), super::b()), (3, 9)); } }
