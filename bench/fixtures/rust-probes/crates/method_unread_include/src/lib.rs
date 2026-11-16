#![allow(dead_code, unused)]
// `include!` at the module level brings the items of another file in (a build script's generated file is brought the same way). The
// compiler's answer for the call is the repo's method; the file is source ripple did not read, so the call is not an edge.
// (rustc rejects a macro_rules! made there that redefines a standard macro, E0659, so the refusal is a rule of the read-whole kind, not
// one that a wrong edge forces: the probe states it.)
include!("../shared/generated.rs");

pub struct S;

impl S {
    pub fn go(&self) -> u8 {
        1
    }
}

pub fn calls(s: &S) -> u8 {
    s.go() //~ go=U:did not read
}

pub fn calls_generated() -> u8 {
    generated()
}
