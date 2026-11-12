#![allow(warnings)]
#[cfg_attr(feature = "d", derive(Default))]
pub struct S { pub x: u32 }
#[cfg(not(feature = "d"))]
impl Default for S { fn default() -> S { S { x: 7 } } }
pub fn a() -> S { S::default() }
#[cfg(test)]
mod t { #[test] fn which() { assert_eq!(super::a().x, 7); } }
