#![allow(dead_code, unused, unexpected_cfgs, non_local_definitions)]
// The same-predicate rule: a `#[cfg(P)]` on the declaration's path is satisfied by another plain `#[cfg(P)]` on the call's path, with the
// identical tokens, read in one crate (tests/test_rs_same_predicate.py states the five conditions). Every type here has a trait `Tr` that gives
// `m` and `make` a default, so that "the call found the impl" and "the call found the trait" are different answers, and each `#[test]` asserts
// which one ran under `cargo test` (cfg(test) on, feature `x` off) and, where the answer changes, under `cargo test --features x`.
// `cargo run --bin show` is the build without `cfg(test)`. build.rs sets the cfg `foo = "a b"`.
//
// Markers, at the end of the line of the call: `m=E` (an edge) or `m=U:reason` (unresolved) after the marker of
// tests/test_rs_method_probes.py for a method call, and the same for a path call `make` after the at-sign marker of tests/test_rs_shared_cfg.py.
pub trait Tr {
    fn m(&self) -> &'static str {
        "Tr::m"
    }
    fn make() -> &'static str {
        "Tr::make"
    }
}

// ---- the calls that are edges

// ripgrep's shape: the impl in a file declared `#[cfg(test)] mod testutil;`, the calls in the sibling `#[cfg(test)] mod tests;`
pub struct F1;
impl Tr for F1 {}
#[cfg(test)]
mod testutil;
#[cfg(test)]
mod tests;

// the tokens are the same with other spacing and a comment between them
pub struct E2;
impl Tr for E2 {}
#[cfg(all(test, not(any())))]
mod e2_impl {
    impl crate::E2 {
        pub fn m(&self) -> &'static str {
            "e2::m"
        }
        pub fn make() -> &'static str {
            "e2::make"
        }
    }
}
#[cfg( all( test , /* a comment */ not( any() ) ) )]
mod e2_calls {
    use crate::{E2, Tr};
    fn via(s: &E2) -> &'static str {
        s.m() //~ m=E
    }
    #[test]
    fn calls() {
        assert_eq!(via(&E2), "e2::m");
        assert_eq!(E2::make(), "e2::make"); //@ make=E
    }
}

// two attributes on the declaration's path and two on the call's, each matched, in the other order
pub struct E3;
impl Tr for E3 {}
#[cfg(test)]
mod e3_x {
    #[cfg(not(any()))]
    mod e3_y {
        impl crate::E3 {
            pub fn m(&self) -> &'static str {
                "e3::m"
            }
            pub fn make() -> &'static str {
                "e3::make"
            }
        }
    }
}
#[cfg(not(any()))]
mod e3_p {
    #[cfg(test)]
    mod e3_q {
        use crate::{E3, Tr};
        fn via(s: &E3) -> &'static str {
            s.m() //~ m=E
        }
        #[test]
        fn calls() {
            assert_eq!(via(&E3), "e3::m");
            assert_eq!(E3::make(), "e3::make"); //@ make=E
        }
    }
}

// the impl's own attribute and the call's module
pub struct E4;
impl Tr for E4 {}
#[cfg(test)]
impl E4 {
    pub fn m(&self) -> &'static str {
        "e4::m"
    }
    pub fn make() -> &'static str {
        "e4::make"
    }
}
#[cfg(test)]
mod e4_calls {
    use crate::{E4, Tr};
    fn via(s: &E4) -> &'static str {
        s.m() //~ m=E
    }
    #[test]
    fn calls() {
        assert_eq!(via(&E4), "e4::m");
        assert_eq!(E4::make(), "e4::make"); //@ make=E
    }
}

// ---- condition 2: a plain cfg written in place

// the declaration's module is under a `cfg_attr` that adds a cfg in a test build: with `cfg(test)` on and the feature `x` off, the impl is gone
// and the call, under `cfg(test)`, finds the trait's method
pub struct R2a;
impl Tr for R2a {}
#[cfg_attr(test, cfg(feature = "x"))]
mod r2a_impl {
    impl crate::R2a {
        pub fn m(&self) -> &'static str {
            "r2a::m"
        }
        pub fn make() -> &'static str {
            "r2a::make"
        }
    }
}
#[cfg(test)]
mod r2a_calls {
    use crate::{R2a, Tr};
    fn via(s: &R2a) -> &'static str {
        s.m() //~ m=U:cfg
    }
    fn make() -> &'static str {
        R2a::make() //@ make=U:cfg
    }
    #[test]
    fn calls() {
        let on = cfg!(feature = "x");
        assert_eq!(via(&R2a), if on { "r2a::m" } else { "Tr::m" });
        assert_eq!(make(), if on { "r2a::make" } else { "Tr::make" });
    }
}

// the call's module is under a `cfg_attr` that adds `cfg(test)` only with the feature `x`, the declaration's under the plain `cfg(test)`: with
// `x` off the call is in every build, and in the build without `cfg(test)` (`cargo run --bin show`) it finds the trait's method. A `cfg_attr` is
// no predicate the rule reads (with `x` on it would be one: recall lost there, never an edge drawn)
pub struct R2b;
impl Tr for R2b {}
#[cfg(test)]
mod r2b_impl {
    impl crate::R2b {
        pub fn m(&self) -> &'static str {
            "r2b::m"
        }
        pub fn make() -> &'static str {
            "r2b::make"
        }
    }
}
#[cfg_attr(feature = "x", cfg(test))]
pub mod r2b_calls {
    use crate::{R2b, Tr};
    pub fn via(s: &R2b) -> &'static str {
        s.m() //~ m=U:cfg
    }
    pub fn make() -> &'static str {
        R2b::make() //@ make=U:cfg
    }
}

// `cfg!` is an expression, not an attribute: the call is in the build with or without cfg(test), and finds the trait's method without it
pub struct R2c;
impl Tr for R2c {}
#[cfg(test)]
mod r2c_impl {
    impl crate::R2c {
        pub fn m(&self) -> &'static str {
            "r2c::m"
        }
        pub fn make() -> &'static str {
            "r2c::make"
        }
    }
}
pub mod r2c_calls {
    use crate::{R2c, Tr};
    pub fn via(s: &R2c) -> &'static str {
        if cfg!(test) {
            s.m() //~ m=U:cfg
        } else {
            s.m() //~ m=U:cfg
        }
    }
    pub fn make() -> &'static str {
        if cfg!(test) {
            R2c::make() //@ make=U:cfg
        } else {
            R2c::make() //@ make=U:cfg
        }
    }
}
#[cfg(test)]
#[test]
fn r2c_runs() {
    assert_eq!(r2c_calls::via(&R2c), "r2c::m");
    assert_eq!(r2c_calls::make(), "r2c::make");
}

// ---- condition 3: tokens, compared as written (the build script sets `foo = "a b"`: the call under it exists in every build)

pub struct R3a;
impl Tr for R3a {}
#[cfg(foo = "a  b")]
mod r3a_impl {
    impl crate::R3a {
        pub fn m(&self) -> &'static str {
            "r3a::m"
        }
        pub fn make() -> &'static str {
            "r3a::make"
        }
    }
}
#[cfg(foo = "a b")]
pub mod r3a_calls {
    use crate::{R3a, Tr};
    pub fn via(s: &R3a) -> &'static str {
        s.m() //~ m=U:cfg
    }
    pub fn make() -> &'static str {
        R3a::make() //@ make=U:cfg
    }
}

pub struct R3b;
impl Tr for R3b {}
#[cfg(foo = "ab")]
mod r3b_impl {
    impl crate::R3b {
        pub fn m(&self) -> &'static str {
            "r3b::m"
        }
        pub fn make() -> &'static str {
            "r3b::make"
        }
    }
}
#[cfg(foo = "a b")]
pub mod r3b_calls {
    use crate::{R3b, Tr};
    pub fn via(s: &R3b) -> &'static str {
        s.m() //~ m=U:cfg
    }
    pub fn make() -> &'static str {
        R3b::make() //@ make=U:cfg
    }
}

// a raw string for the same string: one cfg to rustc, two tokens to ripple (recall lost)
pub struct R3c;
impl Tr for R3c {}
#[cfg(foo = "a b")]
mod r3c_impl {
    impl crate::R3c {
        pub fn m(&self) -> &'static str {
            "r3c::m"
        }
        pub fn make() -> &'static str {
            "r3c::make"
        }
    }
}
#[cfg(foo = r"a b")]
mod r3c_calls {
    use crate::{R3c, Tr};
    fn via(s: &R3c) -> &'static str {
        s.m() //~ m=U:cfg
    }
    #[test]
    fn calls() {
        assert_eq!(via(&R3c), "r3c::m");
        assert_eq!(R3c::make(), "r3c::make"); //@ make=U:cfg
    }
}

// the same tokens in another order (logically equal; no boolean reasoning)
pub struct R3d;
impl Tr for R3d {}
#[cfg(all(test, not(any())))]
mod r3d_impl {
    impl crate::R3d {
        pub fn m(&self) -> &'static str {
            "r3d::m"
        }
        pub fn make() -> &'static str {
            "r3d::make"
        }
    }
}
#[cfg(all(not(any()), test))]
mod r3d_calls {
    use crate::{R3d, Tr};
    fn via(s: &R3d) -> &'static str {
        s.m() //~ m=U:cfg
    }
    #[test]
    fn calls() {
        assert_eq!(via(&R3d), "r3d::m");
        assert_eq!(R3d::make(), "r3d::make"); //@ make=U:cfg
    }
}

// ---- condition 4: every other attribute of the declaration's path is matched, and one that is not refuses

// a module inside the impl's, under the feature `x`: with `x` off the impl is gone, and the call (under `cfg(test)` only) finds the trait's
pub struct R4a;
impl Tr for R4a {}
#[cfg(test)]
mod r4a_x {
    #[cfg(feature = "x")]
    mod r4a_y {
        impl crate::R4a {
            pub fn m(&self) -> &'static str {
                "r4a::m"
            }
            pub fn make() -> &'static str {
                "r4a::make"
            }
        }
    }
}
#[cfg(test)]
mod r4a_calls {
    use crate::{R4a, Tr};
    fn via(s: &R4a) -> &'static str {
        s.m() //~ m=U:cfg
    }
    fn make() -> &'static str {
        R4a::make() //@ make=U:cfg
    }
    #[test]
    fn calls() {
        let on = cfg!(feature = "x");
        assert_eq!(via(&R4a), if on { "r4a::m" } else { "Tr::m" });
        assert_eq!(make(), if on { "r4a::make" } else { "Tr::make" });
    }
}

// the impl under the feature on the impl itself, the module under `cfg(test)`
pub struct R4b;
impl Tr for R4b {}
#[cfg(test)]
mod r4b_impl {
    #[cfg(feature = "x")]
    impl crate::R4b {
        pub fn m(&self) -> &'static str {
            "r4b::m"
        }
        pub fn make() -> &'static str {
            "r4b::make"
        }
    }
}
#[cfg(test)]
mod r4b_calls {
    use crate::{R4b, Tr};
    fn via(s: &R4b) -> &'static str {
        s.m() //~ m=U:cfg
    }
    fn make() -> &'static str {
        R4b::make() //@ make=U:cfg
    }
    #[test]
    fn calls() {
        let on = cfg!(feature = "x");
        assert_eq!(via(&R4b), if on { "r4b::m" } else { "Tr::m" });
        assert_eq!(make(), if on { "r4b::make" } else { "Tr::make" });
    }
}

// the method under the feature `x`, the impl's module under `cfg(test)`: one of the call's attributes matches the module, and the method's own is not matched
pub struct R4c;
impl Tr for R4c {}
#[cfg(test)]
mod r4c_impl {
    impl crate::R4c {
        #[cfg(feature = "x")]
        pub fn m(&self) -> &'static str {
            "r4c::m"
        }
        #[cfg(feature = "x")]
        pub fn make() -> &'static str {
            "r4c::make"
        }
    }
}
#[cfg(test)]
mod r4c_calls {
    use crate::{R4c, Tr};
    fn via(s: &R4c) -> &'static str {
        s.m() //~ m=U:cfg
    }
    fn make() -> &'static str {
        R4c::make() //@ make=U:cfg
    }
    #[test]
    fn calls() {
        let on = cfg!(feature = "x");
        assert_eq!(via(&R4c), if on { "r4c::m" } else { "Tr::m" });
        assert_eq!(make(), if on { "r4c::make" } else { "Tr::make" });
    }
}

// ---- condition 5: another definition that could win, with the predicate on

// an impl under `not(test)` of the same type: with `cfg(test)` on only the module's exists (legal), and ripple sees two definitions
pub struct R5a;
impl Tr for R5a {}
#[cfg(not(test))]
impl R5a {
    pub fn m(&self) -> &'static str {
        "r5a::not_test::m"
    }
    pub fn make() -> &'static str {
        "r5a::not_test::make"
    }
}
#[cfg(test)]
mod r5a_impl {
    impl crate::R5a {
        pub fn m(&self) -> &'static str {
            "r5a::test::m"
        }
        pub fn make() -> &'static str {
            "r5a::test::make"
        }
    }
}
#[cfg(test)]
mod r5a_calls {
    use crate::{R5a, Tr};
    fn via(s: &R5a) -> &'static str {
        s.m() //~ m=U:cfg
    }
    #[test]
    fn calls() {
        assert_eq!(via(&R5a), "r5a::test::m");
        assert_eq!(R5a::make(), "r5a::test::make"); //@ make=U:cfg
    }
}

// a trait method at an earlier probe step under the predicate: `s: R5b` by value finds `ByVal::m(self)` at the first step, before the inherent
// `m(&self)` (the autoref step)
pub struct R5b;
impl Tr for R5b {}
pub trait ByVal {
    fn m(self) -> &'static str;
}
#[cfg(test)]
mod r5b_impl {
    impl crate::ByVal for crate::R5b {
        fn m(self) -> &'static str {
            "r5b::ByVal::m"
        }
    }
    impl crate::R5b {
        pub fn m(&self) -> &'static str {
            "r5b::inherent::m"
        }
    }
}
#[cfg(test)]
mod r5b_calls {
    use crate::{ByVal, R5b, Tr};
    fn via(s: R5b) -> &'static str {
        s.m() //~ m=U
    }
    #[test]
    fn calls() {
        assert_eq!(via(R5b), "r5b::ByVal::m");
    }
}

// a `use` under the predicate that makes the receiver's type another one: in the call's module `R5c` is `other::R5c`
pub struct R5c;
impl Tr for R5c {}
pub mod other {
    pub struct R5c;
    impl crate::Tr for R5c {}
}
#[cfg(test)]
mod r5c_impl {
    impl crate::R5c {
        pub fn m(&self) -> &'static str {
            "r5c::m"
        }
        pub fn make() -> &'static str {
            "r5c::make"
        }
    }
}
#[cfg(test)]
mod r5c_calls {
    use crate::Tr;
    #[cfg(test)]
    use crate::other::R5c;
    fn via(s: &R5c) -> &'static str {
        s.m() //~ m=U:trait method
    }
    #[test]
    fn calls() {
        assert_eq!(via(&R5c), "Tr::m");
        assert_eq!(R5c::make(), "Tr::make"); //@ make=E
    }
}

// the type itself under another predicate than the call's (a build without `x` has no such type, so this one is built with it only)
#[cfg(feature = "x")]
pub struct R5d;
#[cfg(feature = "x")]
impl Tr for R5d {}
#[cfg(test)]
mod r5d_impl {
    #[cfg(feature = "x")]
    impl crate::R5d {
        pub fn m(&self) -> &'static str {
            "r5d::m"
        }
        pub fn make() -> &'static str {
            "r5d::make"
        }
    }
}
#[cfg(all(test, feature = "x"))]
mod r5d_calls {
    use crate::{R5d, Tr};
    fn via(s: &R5d) -> &'static str {
        s.m() //~ m=U:cfg
    }
    #[test]
    fn calls() {
        assert_eq!(via(&R5d), "r5d::m");
        assert_eq!(R5d::make(), "r5d::make"); //@ make=U:cfg
    }
}

// ---- what the rule does not reach

// the receiver's own `let` and parameter under the predicate
pub struct L1;
impl Tr for L1 {}
#[cfg(test)]
mod l1_impl {
    impl crate::L1 {
        pub fn m(&self) -> &'static str {
            "l1::m"
        }
    }
}
#[cfg(test)]
mod l1_calls {
    use crate::{L1, Tr};
    fn cfg_let() -> &'static str {
        #[cfg(test)]
        let s: &L1 = &L1;
        s.m() //~ m=U:cfg
    }
    fn cfg_param(#[cfg(test)] s: &L1) -> &'static str {
        s.m() //~ m=U:cfg
    }
    #[test]
    fn calls() {
        assert_eq!(cfg_let(), "l1::m");
        assert_eq!(cfg_param(&L1), "l1::m");
    }
}

// a `#[test]` function: it exists only in a test build, and no `cfg(test)` is written
pub struct L2;
impl Tr for L2 {}
#[cfg(test)]
mod l2_impl {
    impl crate::L2 {
        pub fn m(&self) -> &'static str {
            "l2::m"
        }
        pub fn make() -> &'static str {
            "l2::make"
        }
    }
}
#[test]
fn l2_calls() {
    let s: &L2 = &L2;
    assert_eq!(s.m(), "l2::m"); //~ m=U:cfg
    assert_eq!(L2::make(), "l2::make"); //@ make=U:cfg
}

// ---- what a build without `cfg(test)` answers (`cargo run --bin show`): the calls that are in it

pub fn answers() -> Vec<(&'static str, &'static str)> {
    let mut out = vec![
        ("r2c method", r2c_calls::via(&R2c)),
        ("r2c path", r2c_calls::make()),
        ("r3a method", r3a_calls::via(&R3a)),
        ("r3a path", r3a_calls::make()),
        ("r3b method", r3b_calls::via(&R3b)),
        ("r3b path", r3b_calls::make()),
    ];
    #[cfg(any(test, not(feature = "x")))]
    {
        out.push(("r2b method", r2b_calls::via(&R2b)));
        out.push(("r2b path", r2b_calls::make()));
    }
    out
}

#[cfg(test)]
#[test]
fn the_calls_the_declaration_is_not_under_find_the_trait_s_method_even_with_test_on() {
    let want = [
        ("r2b method", "r2b::m"),
        ("r2b path", "r2b::make"),
        ("r2c method", "r2c::m"),
        ("r2c path", "r2c::make"),
        ("r3a method", "Tr::m"),
        ("r3a path", "Tr::make"),
        ("r3b method", "Tr::m"),
        ("r3b path", "Tr::make"),
    ];
    let got = answers();
    for (name, ran) in want {
        assert!(got.contains(&(name, ran)), "{name}: {got:?}");
    }
}
