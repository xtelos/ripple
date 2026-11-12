#![allow(warnings)]
#[macro_use]
extern crate extmacros;
use std::cell::Cell;
thread_local! { static RAN: Cell<u32> = Cell::new(0); }
fn mark(n: u32) { RAN.with(|r| r.set(r.get() + n)); }
pub fn ran() -> u32 { RAN.with(|r| r.get()) }
fn repo_fn() -> bool { mark(10000); true }
fn repo_fn2() -> u32 { mark(100); 5 }
pub fn e() { assert!(repo_fn()); }
pub fn d() -> usize { vec![repo_fn2()] }
fn helper() -> u32 { 1 }
pub fn f() -> u32 { dbg!(helper); helper() }
#[cfg(test)]
mod t {
    use super::*;
    #[test]
    fn which() { e(); assert_eq!(d(), 0); assert_eq!(ran(), 0); assert_eq!(f(), 99); }
}
