#![allow(dead_code, unused)]
// `use std::include as concat;` makes the name of a standard macro that brings no file the name of one that does: the nested
// `concat!` of `gen`'s body includes `../shared/extra.rs`. A nested macro written with a standard name is the standard one only where
// no `use` of the crate gives that name.
use std::include as concat;

macro_rules! gen {
    () => {
        concat!("../shared/extra.rs");
    };
}
gen!();

pub mod b {
    pub struct S;
    impl S {
        pub fn m(&self) -> &'static str {
            "S::m"
        }
    }
    pub fn f(s: &S) -> &'static str {
        s.m() //~ m=U:did not read
    }
}

#[cfg(test)]
mod proof {
    #[test]
    fn the_file_is_included() {
        assert_eq!(super::extra(), 9);
        assert_eq!(super::b::f(&super::b::S), "S::m");
    }
}
