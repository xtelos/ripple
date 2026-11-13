#![allow(warnings)]
use fakemacros::test;
fn one() -> u32 { 1 }
fn two() -> u32 { 2 }
pub struct S;
impl S {
    #[cfg_attr(all(), test)]
    pub fn m() -> u32 { one() }
}
#[cfg(test)]
mod t {
    #[test]
    fn which() {
        assert_eq!(super::S::m(), 2);
    }
}
