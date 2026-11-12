#![allow(warnings)]
macro_rules! decl { () => { fn d() -> u32 { 7 } } }
pub trait Tr { decl!(); fn e() -> u32 { 1 } }
pub struct S;
impl Tr for S {}
pub fn c1() -> u32 { <S as Tr>::d() }
pub fn c2() -> u32 { S::d() }
pub fn c3() -> u32 { <S as Tr>::e() }
pub fn c4() -> u32 { S::e() }
#[cfg(test)]
mod t { #[test] fn which() { assert_eq!((super::c1(), super::c2(), super::c3(), super::c4()), (7, 7, 1, 1)); } }
