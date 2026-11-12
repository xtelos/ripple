#![allow(warnings)]
mod a;
mod nm;
pub fn run() -> u32 { a::f() + a::b::g() + a::c::h() + nm::n() + nm::deep::d() }
