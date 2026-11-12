#![allow(warnings)]
mod a {
    pub fn f() -> u32 { 1 }
}
mod b {
    use a::f;
    pub fn g() -> u32 { f() + ::a::f() }
}
pub fn run() -> u32 { b::g() + a::f() }
