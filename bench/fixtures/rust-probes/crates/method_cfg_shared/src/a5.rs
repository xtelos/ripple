#! /* c */ [cfg(test)]
// An inner attribute that tree-sitter reads as a shebang (rustc reads `#!` not followed by `[`, comments aside, as one): the cfg of the
// file, and the same ancestor for the type, the impl and the calls in it.
use super::Tr;

pub struct S;
impl Tr for S {}
impl S {
    pub fn m(&self) -> &'static str {
        "a5::m"
    }
    pub fn make() -> &'static str {
        "a5::make"
    }
}

fn via(s: &S) -> &'static str {
    s.m() //~ m=E
}

#[test]
fn a5_calls() {
    assert_eq!(via(&S), "a5::m");
    assert_eq!(S::make(), "a5::make"); //@ make=E
}
