#![allow(dead_code, unused)]
// A repo macro `gen` whose body invokes `helper!`. A `helper` macro_rules! of the repo exists, in another module, where it is not in
// textual scope of the invocation; there `helper!` is the one `#[macro_use] extern crate filemacro` brings, which emits `mod x;` from a
// file ripple does not read. A repo macro a body names is the repo's only where its definition is in textual scope at the invocation.
#[macro_use]
extern crate filemacro;

macro_rules! gen {
    () => {
        helper!();
    };
}
gen!();

pub mod later {
    macro_rules! helper {
        () => {};
    }
}

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

pub fn g() -> u8 {
    x::xf()
}
