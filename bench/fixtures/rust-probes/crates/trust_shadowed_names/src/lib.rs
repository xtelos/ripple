#![allow(warnings)]
fn one() -> u32 { 1 }
fn two() -> u32 { 2 }

// what the crate manifest calls tokio is the published crate, but the name `tokio` or `serial` means something else in
// these modules, or the attribute is not where the rule applies: each function that fakemacros rewrites is a call of
// `two` that was written as a call of `one`
pub mod local_mod {
    mod tokio {
        pub use fakemacros::main;
    }
    #[tokio::main]
    pub fn a() -> u32 { super::one() }
}
pub mod use_alias {
    use fakemacros as tokio;
    #[tokio::main]
    pub fn b() -> u32 { super::one() }
}
pub mod serial_other_import {
    use fakemacros::serial;
    #[serial]
    pub fn c() -> u32 { super::one() }
}
pub mod beside_another_macro {
    #[tokio::main]
    #[fakemacros::rewrite]
    pub async fn d() -> u32 { super::one() }
}

// the published macros, on what the rule leaves alone: an impl method (a published serial_test drops its `self` and moves
// the body), so the body means what it says, and ripple still does not read it as an ordinary function's
pub mod impl_methods {
    use serial_test::serial;
    pub struct S;
    impl S {
        #[tokio::main]
        pub async fn e() -> u32 { super::one() }
        #[serial]
        pub fn f() { assert_eq!(super::one(), 1) }
    }
}

#[cfg(test)]
mod t {
    use super::*;

    #[test]
    fn which() {
        assert_eq!((local_mod::a(), use_alias::b(), serial_other_import::c(), beside_another_macro::d()), (2, 2, 2, 2));
        assert_eq!(impl_methods::S::e(), 1);
        impl_methods::S::f();
    }
}
