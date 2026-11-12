#![allow(warnings)]
macro_rules! thread_local { ($t:ident) => { impl $t { pub fn f() -> u32 { 2 } } } }
pub trait T { fn f() -> u32; }
pub struct S;
impl T for S { fn f() -> u32 { 1 } }
thread_local!(S);
pub fn call() -> u32 { S::f() }
#[cfg(test)]
mod t { #[test] fn which() { assert_eq!(super::call(), 2); } }
