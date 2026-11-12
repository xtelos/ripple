#![allow(warnings)]
pub trait Conv<Y> { fn conv() -> u32; }
pub struct V;
impl Conv<u8> for V { fn conv() -> u32 { 1 } }
macro_rules! blanket { () => { impl<Y> Conv<Y> for Y { fn conv() -> u32 { 2 } } } }
blanket!();
pub fn to_v() -> u32 { <V as Conv<V>>::conv() }
pub fn to_u8() -> u32 { <V as Conv<u8>>::conv() }
#[cfg(test)]
mod t { #[test] fn which() { assert_eq!((super::to_v(), super::to_u8()), (2, 1)); } }
