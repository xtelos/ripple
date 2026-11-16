#![allow(dead_code, unused, unexpected_cfgs)]
// The other definition, made by a macro: an inherent `m` of the type that the tool does not see, outside the module that holds the call and the
// impl it is read from. Under `cfg(not(test))` it is legal beside the module's own (with test on it is not there), and the call finds the module's
// impl. Under no cfg it would be a second inherent `m` of the type with test on: E0592 (bench/fixtures/rust-probes/compile_fail).
// Markers (tests/test_rs_shared_cfg.py): a path call says `make=E` (an edge to the impl's function, which rust-analyzer resolves the same way)
// or `make=U:cfg` after the at-sign marker.
pub trait Tr {
    fn make() -> &'static str {
        "Tr::make"
    }
}

macro_rules! make_impl {
    ($t:ty, $n:expr) => {
        impl $t {
            pub fn make() -> &'static str {
                $n
            }
        }
    };
}

pub struct S;
impl Tr for S {}

mod outer {
    #[cfg(not(test))]
    make_impl!(crate::S, "S::macro::make");
}

#[cfg(test)]
mod inner {
    use super::S;
    impl S {
        pub fn make() -> &'static str {
            "inner::make"
        }
    }
    #[test]
    fn calls() {
        assert_eq!(S::make(), "inner::make"); //@ make=E
    }
}

pub fn without_test() -> &'static str {
    S::make() //@ make=U:cfg
}

#[cfg(test)]
#[test]
fn with_test_the_modules_impl_is_what_the_plain_call_finds() {
    assert_eq!(without_test(), "inner::make");
}
