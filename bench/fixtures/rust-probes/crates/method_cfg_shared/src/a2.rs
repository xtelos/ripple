// Declared `#[cfg(test)] mod a2;` in lib.rs: the attribute is on the `mod` declaration in the parent's file, and the type, the impl and both
// calls are in this file. Every item here is under that one attribute, as the same ancestor.
use super::Tr;

pub struct S;
impl Tr for S {}
impl S {
    pub fn m(&self) -> &'static str {
        "a2::m"
    }
    pub fn make() -> &'static str {
        "a2::make"
    }
}

fn via(s: &S) -> &'static str {
    s.m() //~ m=E
}

#[test]
fn a2_calls() {
    assert_eq!(via(&S), "a2::m");
    assert_eq!(S::make(), "a2::make"); //@ make=E
}

// a child file of this one, declared with no attribute of its own: it is under a2's attribute, which is above the impl it holds too
mod inner;
// the impl is in the child file and the call is here: the impl is under a2's attribute only, which the call is under
pub struct T;
impl Tr for T {}

fn via_t(t: &T) -> &'static str {
    t.m() //~ m=E
}

#[test]
fn a2_child_impl() {
    assert_eq!(via_t(&T), "a2::inner::T::m");
    assert_eq!(T::make(), "a2::inner::T::make"); //@ make=E
}

// a child file under a cfg of its own: the impl it holds is under that cfg, which a call here is not under
#[cfg(not(any()))]
mod gated;
pub struct U;
impl Tr for U {}

fn via_u(u: &U) -> &'static str {
    u.m() //~ m=U:cfg
}

#[test]
fn a2_gated_impl() {
    assert_eq!(via_u(&U), "a2::gated::U::m");
    assert_eq!(U::make(), "a2::gated::U::make"); //@ make=U:cfg
}
