use super::{S, T};

impl T {
    pub fn m(&self) -> &'static str {
        "a2::inner::T::m"
    }
    pub fn make() -> &'static str {
        "a2::inner::T::make"
    }
}

// a call in a child file of a2, of an impl in a2: the call is under a2's attribute and the impl is under it too
fn via_s(s: &S) -> &'static str {
    s.m() //~ m=E
}

#[test]
fn a2_inner_calls() {
    assert_eq!(via_s(&S), "a2::m");
    assert_eq!(S::make(), "a2::make"); //@ make=E
}
