#![allow(warnings)]
mod other {
    pub fn one() -> u32 { 1 }
}
use other::*;

// a derive that is not the standard library's can emit any item beside the type: this one makes `one`, which beats the glob
#[derive(genmacros::Emit)]
pub struct S;

pub fn f() -> u32 { one() }

#[cfg(test)]
mod t {
    #[test]
    fn which() {
        assert_eq!(super::f(), 2);
    }
}
