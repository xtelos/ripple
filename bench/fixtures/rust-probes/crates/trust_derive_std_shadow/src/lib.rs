#![allow(warnings)]
// a module called std: `use std::fmt::Debug;` is this module's re-export of another derive, not the standard library's
mod std {
    pub mod fmt {
        pub use genmacros::Debug;
    }
}
mod other {
    pub fn one() -> u32 { 1 }
}
use other::*;
use std::fmt::Debug;

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
