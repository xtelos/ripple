#![allow(warnings)]
pub trait T { fn f() -> u32; }
pub struct Bar;
impl T for Bar { fn f() -> u32 { 1 } }
mod imp {
    use super::*;
    thread_local! { static X: u32 = 0; }
    impl Bar { pub fn f() -> u32 { 2 } }
}
pub fn call() -> u32 { Bar::f() }
#[cfg(test)]
mod t { #[test] fn w() { assert_eq!(super::call(), 2); } }
