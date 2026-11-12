#![allow(warnings)]
#[macro_use]
extern crate mk;
use mk::Make;
pub struct Z;
impl_make!(Z, 3);
pub struct Y;
impl Make for Y { fn make() -> u32 { 4 } }
pub fn z_type() -> u32 { Z::make() }
pub fn y_type() -> u32 { Y::make() }
pub fn y_q() -> u32 { <Y as Make>::make() }
#[cfg(test)]
mod t { #[test] fn which() { assert_eq!((super::z_type(), super::y_type(), super::y_q()), (3, 4, 4)); } }
