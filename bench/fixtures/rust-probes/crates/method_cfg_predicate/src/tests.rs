use crate::{Tr, F1};

fn via(s: &F1) -> &'static str {
    s.m() //~ m=E
}

#[test]
fn calls() {
    assert_eq!(via(&F1), "f1::m");
    assert_eq!(F1::make(), "f1::make"); //@ make=E
}
