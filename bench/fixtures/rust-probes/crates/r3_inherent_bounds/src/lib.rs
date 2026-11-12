#![allow(warnings)]
pub struct W<T>(T);
impl<T: Clone> W<T> { pub fn make() -> u32 { 1 } }
impl W<String> { pub fn other() -> u32 { 2 } }
pub trait Tr { fn make() -> u32; }
impl Tr for W<u8> { fn make() -> u32 { 3 } }
pub fn m1() -> u32 { W::<u16>::make() }
pub fn m2() -> u32 { W::<u8>::make() }
pub fn o() -> u32 { W::<String>::other() }
#[cfg(test)]
mod t { #[test] fn which() { assert_eq!((super::m1(), super::m2(), super::o()), (1, 1, 2)); } }
