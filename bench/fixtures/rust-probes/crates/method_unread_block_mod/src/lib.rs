#![allow(dead_code, unused)]
// A `mod` written in a function body (it needs a `#[path]`) is a file ripple does not read; its macros stay in the block, its
// `#[macro_use]` ones for the rest of it. The call is not an edge, though the compiler's answer is the repo's method.
pub struct S;

impl S {
    pub fn go(&self) -> u8 {
        1
    }
}

pub fn setup() -> u8 {
    #[path = "extra.rs"]
    mod extra;
    extra::helper()
}

pub fn calls(s: &S) -> u8 {
    s.go() //~ go=U:did not read
}
