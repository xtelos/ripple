#![allow(dead_code, unused, unexpected_cfgs, non_local_definitions)]
// A cfg that is on the call and on the declaration both, through an ancestor they share. An inherent method or associated function whose impl
// (or anything around the impl) is under a `#[cfg]` does not exist when the cfg is off, and the call may find a trait's method of the name
// instead; but a call that is itself under that same attribute, as the same attribute on the same syntactic ancestor, exists only when
// the declaration does. Every type here has a trait `Tr` that gives `m` and `make` a default, so that "the call found the impl" and "the
// call found the trait" are different answers, and each `#[test]` asserts which one ran (`cargo test`, where `cfg(test)` is on). `cargo
// run --bin show` is the build without `cfg(test)`: where a call and a declaration are not under one attribute, it prints the trait's.
//
// Markers, at the end of the line of the call: a method call says `m=E` (an edge to the impl's method, which rust-analyzer resolves the same
// way) or `m=U:cfg` (unresolved, with a reason that names the `#[cfg]`) after the marker of tests/test_rs_method_probes.py, a path call the
// same with `make` after the at-sign marker of tests/test_rs_shared_cfg.py.
use std::sync::atomic::{AtomicUsize, Ordering};

pub trait Tr {
    fn m(&self) -> &'static str {
        "Tr::m"
    }
    fn make() -> &'static str {
        "Tr::make"
    }
}

// ---- the shared ancestor is a module: inline, a file, an inner attribute, two attributes, a cfg_attr

#[cfg(test)]
mod a1 {
    use super::Tr;
    pub struct S;
    impl Tr for S {}
    impl S {
        pub fn m(&self) -> &'static str {
            "a1::m"
        }
        pub fn make() -> &'static str {
            "a1::make"
        }
    }
    fn via(s: &S) -> &'static str {
        s.m() //~ m=E
    }
    #[test]
    fn calls() {
        assert_eq!(via(&S), "a1::m");
        assert_eq!(S::make(), "a1::make"); //@ make=E
        println!("{}", S::make()); //@ make=E
    }
}

#[cfg(test)]
mod a2; // the attribute is on the `mod` declaration, and the impl and the calls are in the file

mod a3; // no attribute here: the file's own `#![cfg(test)]`

mod a4 {
    #![cfg(test)]
    use super::Tr;
    pub struct S;
    impl Tr for S {}
    impl S {
        pub fn m(&self) -> &'static str {
            "a4::m"
        }
        pub fn make() -> &'static str {
            "a4::make"
        }
    }
    fn via(s: &S) -> &'static str {
        s.m() //~ m=E
    }
    #[test]
    fn calls() {
        assert_eq!(via(&S), "a4::m");
        assert_eq!(S::make(), "a4::make"); //@ make=E
    }
}

mod a5; // the file's inner attribute is one tree-sitter reads as a shebang

#[cfg(test)]
#[cfg(not(any()))]
mod a6 {
    use super::Tr;
    pub struct S;
    impl Tr for S {}
    impl S {
        pub fn m(&self) -> &'static str {
            "a6::m"
        }
        pub fn make() -> &'static str {
            "a6::make"
        }
    }
    fn via(s: &S) -> &'static str {
        s.m() //~ m=E
    }
    #[test]
    fn calls() {
        assert_eq!(via(&S), "a6::m");
        assert_eq!(S::make(), "a6::make"); //@ make=E
    }
}

#[cfg_attr(not(test), cfg(any()))]
mod a7 {
    use super::Tr;
    pub struct S;
    impl Tr for S {}
    impl S {
        pub fn m(&self) -> &'static str {
            "a7::m"
        }
        pub fn make() -> &'static str {
            "a7::make"
        }
    }
    fn via(s: &S) -> &'static str {
        s.m() //~ m=E
    }
    #[test]
    fn calls() {
        assert_eq!(via(&S), "a7::m");
        assert_eq!(S::make(), "a7::make"); //@ make=E
    }
}

// a `cfg_attr` whose payload is no cfg: the module is there in both builds, and under test the attribute says something about its names.
// The impl and the calls are inside it either way, and rustc finds the inherent method in both builds; ripple does not read what the
// attribute does to the names of the module, and does not take it for something the call shares.
#[cfg_attr(test, no_implicit_prelude)]
mod a8 {
    use super::Tr;
    pub struct S;
    impl Tr for S {}
    impl S {
        pub fn m(&self) -> &'static str {
            "a8::m"
        }
        pub fn make() -> &'static str {
            "a8::make"
        }
    }
    pub fn via(s: &S) -> &'static str {
        s.m() //~ m=U:cfg
    }
    pub fn make() -> &'static str {
        S::make() //@ make=U:cfg
    }
    pub fn s() -> S {
        S
    }
}
#[test]
fn a8_calls() {
    assert_eq!(a8::via(&a8::s()), "a8::m");
    assert_eq!(a8::make(), "a8::make");
}

// the call is in a module inside the one the impl is in, under an attribute of its own: the call is under the impl's attribute and one more
#[cfg(test)]
mod a9 {
    use super::Tr;
    pub struct S;
    impl Tr for S {}
    impl S {
        pub fn m(&self) -> &'static str {
            "a9::m"
        }
        pub fn make() -> &'static str {
            "a9::make"
        }
    }
    #[cfg(not(any()))]
    mod inner {
        use super::S;
        fn via(s: &S) -> &'static str {
            s.m() //~ m=E
        }
        #[test]
        fn calls() {
            assert_eq!(via(&S), "a9::m");
            assert_eq!(S::make(), "a9::make"); //@ make=E
        }
    }
}

// the impl is in the inner module, under its own extra attribute, and the call is in the outer one: the call is not under that attribute
#[cfg(test)]
mod a10 {
    use super::Tr;
    pub struct S;
    impl Tr for S {}
    #[cfg(not(any()))]
    mod inner {
        impl super::S {
            pub fn m(&self) -> &'static str {
                "a10::inner::m"
            }
            pub fn make() -> &'static str {
                "a10::inner::make"
            }
        }
    }
    fn via(s: &S) -> &'static str {
        s.m() //~ m=U:cfg
    }
    #[test]
    fn calls() {
        assert_eq!(via(&S), "a10::inner::m");
        assert_eq!(S::make(), "a10::inner::make"); //@ make=U:cfg
    }
}

// both the impl and the call are in the inner module, under both attributes
#[cfg(test)]
mod a19 {
    use super::Tr;
    #[cfg(not(any()))]
    mod inner {
        use super::Tr;
        pub struct S;
        impl Tr for S {}
        impl S {
            pub fn m(&self) -> &'static str {
                "a19::inner::m"
            }
            pub fn make() -> &'static str {
                "a19::inner::make"
            }
        }
        fn via(s: &S) -> &'static str {
            s.m() //~ m=E
        }
        #[test]
        fn calls() {
            assert_eq!(via(&S), "a19::inner::m");
            assert_eq!(S::make(), "a19::inner::make"); //@ make=E
        }
    }
}

// ---- a cfg on a sibling is not an ancestor of the call, but a sibling under the same predicate is under it (the same-predicate rule: see
// method_cfg_predicate for each of its conditions)

pub struct S11;
impl Tr for S11 {}
#[cfg(test)]
mod a11_impl {
    impl crate::S11 {
        pub fn m(&self) -> &'static str {
            "a11::m"
        }
        pub fn make() -> &'static str {
            "a11::make"
        }
    }
}
#[cfg(test)]
mod a11_call {
    use crate::S11;
    fn via(s: &S11) -> &'static str {
        s.m() //~ m=E
    }
    #[test]
    fn calls() {
        assert_eq!(via(&S11), "a11::m");
        assert_eq!(S11::make(), "a11::make"); //@ make=E
    }
}

// two different attributes: with `cfg(test)` and `cfg(debug_assertions)` both on (`cargo test`, a dev build) the call finds the impl, and with
// only `debug_assertions` (`cargo run --bin show`) it finds the trait's method
pub struct S12;
impl Tr for S12 {}
#[cfg(test)]
mod a12_impl {
    impl crate::S12 {
        pub fn m(&self) -> &'static str {
            "a12::m"
        }
        pub fn make() -> &'static str {
            "a12::make"
        }
    }
}
#[cfg(debug_assertions)]
pub mod a12_call {
    use crate::{Tr, S12};
    pub fn via(s: &S12) -> &'static str {
        s.m() //~ m=U:cfg
    }
    pub fn make() -> &'static str {
        S12::make() //@ make=U:cfg
    }
}

// ---- the cfg is on the impl itself, or on the method: a call is under it only inside it

pub struct S13;
impl Tr for S13 {}
#[cfg(test)]
impl S13 {
    pub fn m(&self) -> &'static str {
        "S13::m"
    }
    pub fn make() -> &'static str {
        "S13::make"
    }
}
pub fn via13(s: &S13) -> &'static str {
    s.m() //~ m=U:cfg
}
pub fn make13() -> &'static str {
    S13::make() //@ make=U:cfg
}

pub struct S14;
impl Tr for S14 {}
#[cfg(test)]
impl S14 {
    pub fn m(&self) -> &'static str {
        "S14::m"
    }
    pub fn make() -> &'static str {
        "S14::make"
    }
    pub fn go(&self) -> &'static str {
        self.m() //~ m=E
    }
    pub fn go_path() -> &'static str {
        S14::make() //@ make=E
    }
    pub fn go_self() -> &'static str {
        Self::make() //@ make=E
    }
}
#[test]
fn s14_calls() {
    assert_eq!(S14.go(), "S14::m");
    assert_eq!(S14::go_path(), "S14::make");
    assert_eq!(S14::go_self(), "S14::make");
}

pub struct S15;
impl Tr for S15 {}
impl S15 {
    #![cfg(test)]
    pub fn m(&self) -> &'static str {
        "S15::m"
    }
    pub fn make() -> &'static str {
        "S15::make"
    }
    pub fn go(&self) -> &'static str {
        self.m() //~ m=E
    }
    pub fn go_path() -> &'static str {
        S15::make() //@ make=E
    }
}
pub fn via15(s: &S15) -> &'static str {
    s.m() //~ m=U:cfg
}
pub fn make15() -> &'static str {
    S15::make() //@ make=U:cfg
}
#[test]
fn s15_calls() {
    assert_eq!(S15.go(), "S15::m");
    assert_eq!(S15::go_path(), "S15::make");
}

static N16: AtomicUsize = AtomicUsize::new(0);
pub struct S16;
impl Tr for S16 {}
impl S16 {
    #[cfg(test)]
    pub fn m(&self) -> &'static str {
        if N16.fetch_add(1, Ordering::SeqCst) == 0 {
            self.m() //~ m=E
        } else {
            "S16::m"
        }
    }
    #[cfg(test)]
    pub fn make() -> &'static str {
        if N16.fetch_add(1, Ordering::SeqCst) < 4 {
            S16::make() //@ make=E
        } else {
            "S16::make"
        }
    }
}
pub fn via16(s: &S16) -> &'static str {
    s.m() //~ m=U:cfg
}
pub fn make16() -> &'static str {
    S16::make() //@ make=U:cfg
}
#[test]
fn s16_calls() {
    assert_eq!(via16(&S16), "S16::m");
    assert_eq!(make16(), "S16::make");
}

// ---- the other definition: with the shared cfg on, what else may win

// a trait impl for `&S` with `fn m(self)` beside the inherent `m(&self)`: both apply to a `&S` receiver at the first probe step, and the
// inherent one is taken first
#[cfg(test)]
mod b1 {
    use super::Tr;
    pub struct S;
    impl Tr for S {}
    pub trait ByVal {
        fn m(self) -> &'static str;
    }
    impl ByVal for &S {
        fn m(self) -> &'static str {
            "b1::<&S as ByVal>::m"
        }
    }
    impl S {
        pub fn m(&self) -> &'static str {
            "b1::S::m"
        }
        pub fn make() -> &'static str {
            "b1::S::make"
        }
    }
    fn via(s: &S) -> &'static str {
        s.m() //~ m=E
    }
    #[test]
    fn calls() {
        assert_eq!(via(&S), "b1::S::m");
        assert_eq!(S::m(&S), "b1::S::m"); //@ m=E
        assert_eq!(<&S as ByVal>::m(&S), "b1::<&S as ByVal>::m");
    }
}

// an inherent `m` of the type outside the module, under `cfg(not(test))`: legal (with test on only the inner one exists), and the call
// finds the inner one; two definitions under different attributes, which ripple does not tell apart, so it refuses
pub struct S2;
impl Tr for S2 {}
#[cfg(not(test))]
impl S2 {
    pub fn m(&self) -> &'static str {
        "S2::outer::m"
    }
    pub fn make() -> &'static str {
        "S2::outer::make"
    }
}
#[cfg(test)]
mod b2 {
    use super::S2;
    impl S2 {
        pub fn m(&self) -> &'static str {
            "b2::inner::m"
        }
        pub fn make() -> &'static str {
            "b2::inner::make"
        }
    }
    fn via(s: &S2) -> &'static str {
        s.m() //~ m=U:cfg
    }
    #[test]
    fn calls() {
        assert_eq!(via(&S2), "b2::inner::m");
        assert_eq!(S2::make(), "b2::inner::make"); //@ make=U:cfg
    }
}

// ---- the type itself, an alias, a `use`, a generic impl

#[cfg(test)]
mod c1 {
    use super::Tr;
    pub struct S;
    impl Tr for S {}
    pub type A = S;
    impl S {
        pub fn m(&self) -> &'static str {
            "c1::m"
        }
        pub fn make() -> &'static str {
            "c1::make"
        }
    }
    fn via(a: &A) -> &'static str {
        a.m() //~ m=E
    }
    #[test]
    fn calls() {
        assert_eq!(via(&S), "c1::m");
        assert_eq!(A::make(), "c1::make"); //@ make=E
    }
}

pub struct Outer2;
impl Tr for Outer2 {}
#[cfg(test)]
mod c2 {
    use super::Outer2 as O;
    impl O {
        pub fn m(&self) -> &'static str {
            "c2::m"
        }
        pub fn make() -> &'static str {
            "c2::make"
        }
    }
    fn via(o: &O) -> &'static str {
        o.m() //~ m=E
    }
    #[test]
    fn calls() {
        assert_eq!(via(&O), "c2::m");
        assert_eq!(O::make(), "c2::make"); //@ make=E
    }
}

pub struct Outer3;
impl Tr for Outer3 {}
#[cfg(test)]
mod c3 {
    #[cfg(test)]
    use super::Outer3 as O;
    impl O {
        pub fn m(&self) -> &'static str {
            "c3::m"
        }
        pub fn make() -> &'static str {
            "c3::make"
        }
    }
    fn via(o: &O) -> &'static str {
        o.m() //~ m=E
    }
    #[test]
    fn calls() {
        assert_eq!(via(&O), "c3::m");
        assert_eq!(O::make(), "c3::make"); //@ make=E
    }
}

// the type is under a cfg of its own, inside the shared module, with a predicate the call is not under: not one the call is under
#[cfg(test)]
mod c4 {
    use super::Tr;
    #[cfg(not(any()))]
    pub struct S;
    #[cfg(not(any()))]
    impl Tr for S {}
    impl S {
        pub fn m(&self) -> &'static str {
            "c4::m"
        }
        pub fn make() -> &'static str {
            "c4::make"
        }
    }
    fn via(s: &S) -> &'static str {
        s.m() //~ m=U:cfg
    }
    #[test]
    fn calls() {
        assert_eq!(via(&S), "c4::m");
        assert_eq!(S::make(), "c4::make"); //@ make=U:cfg
    }
}

// the same, with the predicate of the shared module: the type exists whenever the call does
#[cfg(test)]
mod c4b {
    use super::Tr;
    #[cfg(test)]
    pub struct S;
    #[cfg(test)]
    impl Tr for S {}
    impl S {
        pub fn m(&self) -> &'static str {
            "c4b::m"
        }
        pub fn make() -> &'static str {
            "c4b::make"
        }
    }
    fn via(s: &S) -> &'static str {
        s.m() //~ m=E
    }
    #[test]
    fn calls() {
        assert_eq!(via(&S), "c4b::m");
        assert_eq!(S::make(), "c4b::make"); //@ make=E
    }
}

#[cfg(test)]
mod c5 {
    use super::Tr;
    pub struct W<T>(pub T);
    impl<T> Tr for W<T> {}
    impl<T> W<T> {
        pub fn m(&self) -> &'static str {
            "c5::m"
        }
        pub fn make() -> &'static str {
            "c5::make"
        }
    }
    fn via(w: &W<u8>) -> &'static str {
        w.m() //~ m=E
    }
    #[test]
    fn calls() {
        assert_eq!(via(&W(1u8)), "c5::m");
        assert_eq!(W::<u8>::make(), "c5::make"); //@ make=E
    }
}

// the type is in one cfg module and the impl too; the call is in a sibling module under the same text of cfg
#[cfg(test)]
mod c6a {
    use super::Tr;
    pub struct S;
    impl Tr for S {}
    impl S {
        pub fn m(&self) -> &'static str {
            "c6::m"
        }
        pub fn make() -> &'static str {
            "c6::make"
        }
    }
}
#[cfg(test)]
mod c6b {
    use super::c6a::S;
    fn via(s: &S) -> &'static str {
        s.m() //~ m=E
    }
    #[test]
    fn calls() {
        assert_eq!(via(&S), "c6::m");
        assert_eq!(S::make(), "c6::make"); //@ make=E
    }
}

// ---- nested functions and blocks

#[cfg(test)]
fn d1() -> (&'static str, &'static str) {
    struct D1;
    impl Tr for D1 {}
    impl D1 {
        fn m(&self) -> &'static str {
            "d1::m"
        }
        fn make() -> &'static str {
            "d1::make"
        }
    }
    let s: &D1 = &D1;
    let a = s.m(); //~ m=E
    let b = D1::make(); //@ make=E
    (a, b)
}
#[test]
fn d1_calls() {
    assert_eq!(d1(), ("d1::m", "d1::make"));
}

pub fn d2() -> (&'static str, &'static str) {
    let mut out = ("", "");
    #[cfg(test)]
    {
        struct D2;
        impl Tr for D2 {}
        impl D2 {
            fn m(&self) -> &'static str {
                "d2::m"
            }
            fn make() -> &'static str {
                "d2::make"
            }
        }
        let s: &D2 = &D2;
        out.0 = s.m(); //~ m=E
        out.1 = D2::make(); //@ make=E
    }
    out
}
#[test]
fn d2_calls() {
    assert_eq!(d2(), ("d2::m", "d2::make"));
}

// the impl is in a cfg block of one function and the call is in another
pub struct S3d;
impl Tr for S3d {}
pub fn d3_setup() {
    #[cfg(test)]
    {
        impl S3d {
            pub fn m(&self) -> &'static str {
                "d3::m"
            }
            pub fn make() -> &'static str {
                "d3::make"
            }
        }
    }
}
pub fn d3(s: &S3d) -> &'static str {
    s.m() //~ m=U:cfg
}
pub fn make3d() -> &'static str {
    S3d::make() //@ make=U:cfg
}
#[test]
fn d3_calls() {
    assert_eq!(d3(&S3d), "d3::m");
    assert_eq!(make3d(), "d3::make");
}

// the receiver's own binding is under a cfg: the `let` and the parameter are not ancestors of the call, which comes after the `let` and is
// in the body of the function the parameter belongs to
#[cfg(test)]
mod d4 {
    use super::Tr;
    pub struct S;
    impl Tr for S {}
    impl S {
        pub fn m(&self) -> &'static str {
            "d4::m"
        }
    }
    fn control(s: &S) -> &'static str {
        s.m() //~ m=E
    }
    fn cfg_let() -> &'static str {
        #[cfg(test)]
        let s: &S = &S;
        s.m() //~ m=U:cfg
    }
    fn cfg_param(#[cfg(test)] s: &S) -> &'static str {
        s.m() //~ m=U:cfg
    }
    #[test]
    fn calls() {
        assert_eq!(control(&S), "d4::m");
        assert_eq!(cfg_let(), "d4::m");
        assert_eq!(cfg_param(&S), "d4::m");
    }
}

// ---- what a build without cfg(test) answers (`cargo run --bin show`): the calls above that are not under their declaration's attribute

pub fn answers() -> Vec<(&'static str, &'static str)> {
    let mut out = vec![("S13 method", via13(&S13)), ("S13 path", make13()), ("S15 method", via15(&S15)), ("S15 path", make15()), ("S16 method", via16(&S16)), ("S16 path", make16())];
    out.push(("S3d method", d3(&S3d)));
    out.push(("S3d path", make3d()));
    #[cfg(debug_assertions)]
    {
        out.push(("a12 method", a12_call::via(&S12)));
        out.push(("a12 path", a12_call::make()));
    }
    out
}

#[cfg(test)]
#[test]
fn every_call_found_the_impl_with_test_on() {
    let want = [
        ("S13 method", "S13::m"),
        ("S13 path", "S13::make"),
        ("S15 method", "S15::m"),
        ("S15 path", "S15::make"),
        ("S16 method", "S16::m"),
        ("S16 path", "S16::make"),
        ("S3d method", "d3::m"),
        ("S3d path", "d3::make"),
        ("a12 method", "a12::m"),
        ("a12 path", "a12::make"),
    ];
    let got = answers();
    for (name, ran) in want {
        assert!(got.contains(&(name, ran)), "{name}: {got:?}");
    }
}
