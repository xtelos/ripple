#![allow(warnings)]
pub trait T { fn f() -> u32; }
pub struct S;
impl T for S { fn f() -> u32 { 1 } }
macro_rules! gen_f { () => { pub fn f() -> u32 { 2 } pub fn g() -> u32 { 3 } } }
impl S {
    gen_f!();
    pub fn via_self() -> u32 { Self::f() + Self::g() }
}
pub fn via_type() -> u32 { S::f() }
pub fn via_qualified() -> u32 { <S as T>::f() }
#[cfg(test)]
mod t {
    use super::*;
    #[test]
    fn which() { assert_eq!(via_type(), 2); assert_eq!(S::via_self(), 5); assert_eq!(via_qualified(), 1); }
}
