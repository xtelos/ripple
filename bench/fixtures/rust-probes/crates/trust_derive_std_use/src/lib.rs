#![allow(warnings)]
use std::fmt::Debug;
mod other {
    pub fn one() -> u32 { 1 }
}
use other::*;

// `use std::fmt::Debug;` gives the name `Debug` the standard library's own derive, which emits nothing: the glob's `one` is what runs
#[derive(Debug)]
pub struct S;

pub fn f() -> u32 { one() }

#[cfg(test)]
mod t {
    #[test]
    fn which() {
        assert_eq!(super::f(), 1);
    }
}
