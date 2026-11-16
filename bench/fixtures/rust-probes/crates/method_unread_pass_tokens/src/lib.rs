#![allow(dead_code, unused)]
// A macro whose body has no `mod` and no `include!` still brings a file in when the invocation's own tokens carry the `mod`: the
// expansion is body plus arguments. Its body is clean, the invocation is not.
macro_rules! pass {
    ($($tokens:tt)*) => {
        $($tokens)*
    };
}

pass! { #[path = "../shared/extra.rs"] mod extra; }

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
