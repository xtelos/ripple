#![allow(dead_code, unused, unexpected_cfgs)]
// Condition 2 of the same-predicate rule: a plain `cfg` written in place, that nothing rewrites. Here the predicate is made by a macro, or the
// item it is in is rewritten by an attribute macro that deletes the `cfg`s inside it (`stripcfg`, crates/_ext). Every type has a trait `Tr` that
// gives `m` and `make` a default; each `#[test]` asserts which one ran under `cargo test`, and `cargo run --bin show` is the build without `cfg(test)`.
// Markers: `m=U` (unresolved) after the method-call marker of tests/test_rs_method_probes.py, and the same for `make` after the path-call one.
use stripcfg::stripcfg;

pub trait Tr {
    fn m(&self) -> &'static str {
        "Tr::m"
    }
    fn make() -> &'static str {
        "Tr::make"
    }
}

// an impl whose predicate a macro takes: it is `cfg(test)` once expanded, and no attribute ripple reads
pub struct R2d;
impl Tr for R2d {}
macro_rules! under {
    ($p:meta) => {
        #[cfg($p)]
        impl R2d {
            pub fn m(&self) -> &'static str {
                "r2d::m"
            }
            pub fn make() -> &'static str {
                "r2d::make"
            }
        }
    };
}
under!(test);
#[cfg(test)]
mod r2d_calls {
    use crate::{R2d, Tr};
    fn via(s: &R2d) -> &'static str {
        s.m() //~ m=U
    }
    #[test]
    fn calls() {
        assert_eq!(via(&R2d), "r2d::m");
        assert_eq!(R2d::make(), "r2d::make"); //@ make=U
    }
}

// the call's functions are written `#[cfg(test)]` inside a module the attribute macro rewrites: it deletes the `cfg`s, so they are in every
// build, and in the build without `cfg(test)` they find the trait's method (the impl, under the real `cfg(test)` outside, is gone)
pub struct M1;
impl Tr for M1 {}
#[cfg(test)]
mod m1_impl {
    impl crate::M1 {
        pub fn m(&self) -> &'static str {
            "m1::m"
        }
        pub fn make() -> &'static str {
            "m1::make"
        }
    }
}
#[stripcfg]
pub mod m1_calls {
    use crate::{M1, Tr};
    #[cfg(test)]
    pub fn via(s: &M1) -> &'static str {
        s.m() // refused: the attribute macro (tests/test_rs_same_predicate.py)
    }
    #[cfg(test)]
    pub fn make() -> &'static str {
        M1::make() //@ make=U
    }
}

// the impl's module is written `#[cfg(test)]` inside the rewritten module too: the `cfg`s of both are deleted, so the impl is in every build
pub struct M2;
impl Tr for M2 {}
#[stripcfg]
pub mod m2 {
    use crate::{M2, Tr};
    #[cfg(test)]
    mod impl_of {
        impl crate::M2 {
            pub fn m(&self) -> &'static str {
                "m2::m"
            }
            pub fn make() -> &'static str {
                "m2::make"
            }
        }
    }
    #[cfg(test)]
    pub fn via(s: &M2) -> &'static str {
        s.m() // refused: the attribute macro (tests/test_rs_same_predicate.py)
    }
    #[cfg(test)]
    pub fn make() -> &'static str {
        M2::make() //@ make=U
    }
}

pub fn answers() -> Vec<(&'static str, &'static str)> {
    vec![
        ("m1 method", m1_calls::via(&M1)),
        ("m1 path", m1_calls::make()),
        ("m2 method", m2::via(&M2)),
        ("m2 path", m2::make()),
    ]
}

#[cfg(test)]
#[test]
fn what_ran() {
    let got = answers();
    assert_eq!(got, [("m1 method", "m1::m"), ("m1 path", "m1::make"), ("m2 method", "m2::m"), ("m2 path", "m2::make")]);
}
