#![allow(dead_code, unused)]
// A `mod` that names no file is under a `#[cfg]` that is off (rustc never looks for the file). Which cfg is on is not known to
// ripple, so whether the module is built, and what it holds, is not: the call is not an edge.
#[cfg(any())]
mod ghost;

pub struct S;

impl S {
    pub fn go(&self) -> u8 {
        1
    }
}

pub fn calls(s: &S) -> u8 {
    s.go() //~ go=U:did not read
}
