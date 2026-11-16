#![allow(dead_code, unused)]
// `pub(crate) use declare as stringify;` makes a repo macro that emits `mod` the macro `stringify!` names: the nested `stringify!()` of
// `gen`'s body is `declare!()`, which declares `mod extra` from a file ripple does not read.
macro_rules! declare {
    () => {
        #[path = "../shared/extra.rs"]
        mod extra;
    };
}
pub(crate) use declare as stringify;

macro_rules! gen {
    () => {
        stringify!();
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
    fn the_file_is_declared() {
        assert_eq!(super::extra::extra(), 9);
        assert_eq!(super::b::f(&super::b::S), "S::m");
    }
}
