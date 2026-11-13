#![allow(warnings)]
use genmacros::Debug;
mod other {
    pub fn one() -> u32 { 1 }
}
use other::*;

// `use genmacros::Debug;` makes `#[derive(Debug)]` another macro (as `use derive_more::Debug;` does), which emits `one`
#[derive(Debug)]
pub struct S;

pub fn f() -> u32 { one() }

#[cfg(test)]
mod t {
    #[test]
    fn which() {
        assert_eq!(super::f(), 2);
    }
}
