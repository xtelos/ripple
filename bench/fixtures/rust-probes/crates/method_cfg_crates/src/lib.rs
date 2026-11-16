#![allow(warnings)]
// One file, `x.rs`, is a module of two crates of this package: this library and the binary (`main.rs` has `mod x;` too). Everything in `x.rs`
// that is under `#[cfg(test)]` is read once per crate, and each crate has its own `test` (a bin's test build links the library built WITHOUT
// `cfg(test)`). The impl and the calls of `x.rs` are under one `#[cfg(test)]` (the attribute at x.rs:1), so in the library's own test build the
// call finds the impl; in the binary's test build the call is to the library's type, and the library has no such impl there: it finds the trait's.
// The same attribute at the same byte of the same file is therefore true in one crate and false in the other, and an attribute is shared only
// by a call and a declaration read in the one crate. (`cargo test` runs both builds: the assertions say which method ran.)
extern crate self as twin;

pub mod x;

pub struct S;

pub trait Tr {
    fn m(&self) -> &'static str {
        "Tr::m"
    }
    fn make() -> &'static str {
        "Tr::make"
    }
}

impl Tr for S {}

#[cfg(test)]
mod proof {
    #[test]
    fn the_library_with_cfg_test_runs_the_inherent_method() {
        assert_eq!(crate::x::inner::call(&crate::S), "S::m");
        assert_eq!(crate::x::inner::call_path(), "S::make");
    }
}
