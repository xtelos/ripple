#![allow(warnings)]
pub trait T { fn f() -> u32; }
pub struct S;
impl T for S { fn f() -> u32 { 1 } }
macro_rules! inner { ($t:ty) => { impl $t { pub fn f() -> u32 { 2 } } } }
macro_rules! outer { ($t:ty) => { inner!($t); } }
outer!(S);
pub fn call() -> u32 { S::f() }
#[cfg(test)]
mod t { #[test] fn which() { assert_eq!(super::call(), 2); } }
