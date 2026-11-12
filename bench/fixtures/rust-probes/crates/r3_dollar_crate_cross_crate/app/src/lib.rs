#![allow(warnings)]
use mk::Make;
pub struct W;
pub struct Z;
impl W { pub fn make() -> u32 { 1 } }
mk::impl_make!(W, 2);
mk::impl_make!(Z, 3);
pub fn w_inherent() -> u32 { W::make() }
pub fn w_trait() -> u32 { <W as Make>::make() }
pub fn z_qualified() -> u32 { <Z as mk::Make>::make() }
pub fn z_type() -> u32 { Z::make() }
#[cfg(test)]
mod t { #[test] fn which() { assert_eq!((super::w_inherent(), super::w_trait(), super::z_qualified(), super::z_type()), (1, 2, 3, 3)); } }
