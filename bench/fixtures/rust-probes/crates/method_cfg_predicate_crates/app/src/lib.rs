#![allow(dead_code, unused, unexpected_cfgs)]
// The library has an impl under `#[cfg(test)]` (in its own unit tests only) and one under its feature `f`. Every other target and member
// of the workspace is a crate of its own: `cfg(test)` is on in its own test build and off in the library it links, and a feature is the
// crate's own. A call there under `#[cfg(test)]` or `#[cfg(feature = "f")]` has the identical predicate of the impl, and the impl is not in
// the library it calls: the call finds the trait's method (every `#[test]` of the workspace asserts which one ran: `cargo test --workspace`
// builds the library with `f` on, since its own member asks for it, and `cargo test -p tool` builds it with `f` off; `F_ON` tells the tests).
pub trait Tr {
    fn m(&self) -> &'static str {
        "Tr::m"
    }
    fn make() -> &'static str {
        "Tr::make"
    }
}

pub const F_ON: bool = cfg!(feature = "f");

pub struct S;
impl Tr for S {}
pub struct F;
impl Tr for F {}

#[cfg(test)]
mod testutil {
    impl crate::S {
        pub fn m(&self) -> &'static str {
            "S::m"
        }
        pub fn make() -> &'static str {
            "S::make"
        }
    }
}

#[cfg(feature = "f")]
mod f_impl {
    impl crate::F {
        pub fn m(&self) -> &'static str {
            "F::m"
        }
        pub fn make() -> &'static str {
            "F::make"
        }
    }
}

// the library's own test build: the same predicate, one crate: edges
#[cfg(test)]
mod tests {
    use crate::{S, Tr};
    fn via(s: &S) -> &'static str {
        s.m() //~ m=E
    }
    #[test]
    fn the_library_runs_its_own_impl() {
        assert_eq!(via(&S), "S::m");
        assert_eq!(S::make(), "S::make"); //@ make=E
    }
}

#[cfg(feature = "f")]
mod f_calls {
    use crate::{F, Tr};
    fn via(s: &F) -> &'static str {
        s.m() //~ m=E
    }
    #[cfg(test)]
    #[test]
    fn the_library_with_f_runs_its_own_impl() {
        assert_eq!(via(&F), "F::m");
        assert_eq!(F::make(), "F::make"); //@ make=E
    }
}
