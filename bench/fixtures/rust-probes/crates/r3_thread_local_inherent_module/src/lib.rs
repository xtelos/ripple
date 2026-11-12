#![allow(warnings)]
pub trait T { fn f() -> u32; }
pub struct Bar;
impl T for Bar { fn f() -> u32 { 1 } }
pub struct Baz;
impl T for Baz { fn f() -> u32 { 10 } }
mod imp {
    use super::*;
    thread_local! { static X: u32 = 0; }
    impl Bar { pub fn f() -> u32 { 2 } }
    impl Baz { pub fn f() -> u32 { 20 } pub fn s() -> u32 { Self::f() } }
}
pub fn call() -> u32 { Bar::f() }
pub fn call_self() -> u32 { Baz::s() }
pub fn call_baz() -> u32 { Baz::f() }
#[cfg(test)]
mod t { #[test] fn which() { assert_eq!((super::call(), super::call_self(), super::call_baz()), (2, 20, 20)); } }
