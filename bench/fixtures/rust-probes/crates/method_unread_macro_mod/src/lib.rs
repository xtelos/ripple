#![allow(dead_code, unused)]
// A macro invoked where items stand expands to `mod extra;`, a module whose file no `mod` of the source names: rustc reads
// shared/extra.rs (outside the crate source directory), ripple does not. (A macro-made module's own macros cannot shadow a standard one, E0659: a rule of the read-whole kind.)
macro_rules! declare {
    ($name:ident) => {
        #[path = "../shared/extra.rs"]
        mod $name;
    };
}

declare!(extra);

pub struct S;

impl S {
    pub fn go(&self) -> u8 {
        1
    }
}

pub fn calls(s: &S) -> u8 {
    s.go() //~ go=U:did not read
}

pub fn calls_extra() -> u8 {
    extra::helper()
}
