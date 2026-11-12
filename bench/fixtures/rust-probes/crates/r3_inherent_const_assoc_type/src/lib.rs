#![allow(warnings)]
pub trait T { fn f() -> u32; }
pub struct C;
fn seven() -> u32 { 7 }
impl C { pub const f: fn() -> u32 = seven; }
impl T for C { fn f() -> u32 { 8 } }
pub trait Ty { type f; fn f() -> u32; }
pub struct D;
impl Ty for D { type f = u8; fn f() -> u32 { 9 } }
pub fn c() -> u32 { C::f() }
pub fn d() -> u32 { D::f() }
pub fn dq() -> u32 { <D as Ty>::f() }
#[cfg(test)]
mod t { #[test] fn which() { assert_eq!((super::c(), super::d(), super::dq()), (7, 9, 9)); } }
