#![allow(warnings)]
use genmacros::*;
mod other {
    pub fn helper() -> u32 { 1 }
}
use other::*;

// the attribute appends a `helper` to this module, which beats the glob; a derive beside it is no helper-attribute reading of `thing`
#[derive(genmacros::D)]
#[thing]
pub struct S;

pub fn f() -> u32 { helper() }

// a `mod genmacros` in a block is never the module that `use genmacros::*` above names: the glob stays one from outside the repo
fn unrelated() {
    mod genmacros {}
}

#[cfg(test)]
mod t {
    #[test]
    fn which() {
        assert_eq!(super::f(), 2);
    }
}
