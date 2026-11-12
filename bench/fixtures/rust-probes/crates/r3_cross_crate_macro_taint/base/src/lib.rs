#![allow(warnings)]
pub trait T { fn d() -> u32 { 1 } }
pub struct S;
impl T for S {}
