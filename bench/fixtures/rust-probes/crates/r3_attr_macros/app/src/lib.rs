#![allow(warnings)]
pub mod a { pub fn helper() -> u32 { 1 } }
pub mod b {
    use crate::a::*;
    #[pm::with_helper]
    fn marker() {}
    pub fn run() -> u32 { helper() }
}
pub fn blk() -> u32 {
    #[pm::with_helper]
    fn marker() {}
    helper()
}
fn helper() -> u32 { 100 }
fn one() -> u32 { 1 }
fn two() -> u32 { 2 }
#[pm::swap_one_for_two]
pub fn rewritten() -> u32 { one() }
pub trait T { fn f() -> u32; }
#[pm::add_f]
pub struct S;
impl T for S { fn f() -> u32 { 1 } }
pub fn s_f() -> u32 { S::f() }
#[derive(pm::WithF)]
pub struct U;
impl T for U { fn f() -> u32 { 1 } }
pub fn u_f() -> u32 { U::f() }
#[pm::identity]
pub fn same() -> u32 { two() }
#[pm::identity]
pub mod inner {
    pub fn calls() -> u32 { super::two() }
}
pub struct Imp;
#[pm::identity]
impl Imp { pub fn make() -> u32 { 5 } }
pub fn imp_make() -> u32 { Imp::make() }
use pm::identity as ident;
#[derive(pm::WithF)]
#[ident]
pub struct V;
impl V { pub fn make() -> u32 { 5 } }
pub fn v_make() -> u32 { V::make() }
#[derive(pm::WithF)]
#[pm_note(x)]
pub struct W;
impl W { pub fn make() -> u32 { 6 } }
pub fn w_make() -> u32 { W::make() }
#[cfg(test)]
mod t {
    use super::*;
    #[test]
    fn which() {
        assert_eq!((b::run(), blk(), rewritten(), s_f(), u_f(), same(), inner::calls(), imp_make()), (2, 2, 2, 2, 2, 2, 2, 5));
        assert_eq!((v_make(), w_make(), V::f(), W::f()), (5, 6, 2, 2));
    }
}
