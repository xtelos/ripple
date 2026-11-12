#![allow(warnings)]
use base::T;
pub struct Local;
macro_rules! over { () => { impl base::T for Local { fn d() -> u32 { 2 } } } }
over!();
pub fn l() -> u32 { Local::d() }
pub fn s_type() -> u32 { base::S::d() }
pub fn s_q() -> u32 { <base::S as base::T>::d() }
#[cfg(test)]
mod t { #[test] fn which() { assert_eq!((super::l(), super::s_type(), super::s_q()), (2, 1, 1)); } }
