#![cfg(test)]
// Declared `mod a3;` with no attribute: the cfg is this file's own inner attribute. It is the same ancestor for everything in the file.
use super::Tr;

pub struct S;
impl Tr for S {}
impl S {
    pub fn m(&self) -> &'static str {
        "a3::m"
    }
    pub fn make() -> &'static str {
        "a3::make"
    }
}

fn via(s: &S) -> &'static str {
    s.m() //~ m=E
}

#[test]
fn a3_calls() {
    assert_eq!(via(&S), "a3::m");
    assert_eq!(S::make(), "a3::make"); //@ make=E
}
