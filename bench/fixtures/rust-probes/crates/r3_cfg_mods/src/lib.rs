#![allow(warnings)]
#[cfg(feature = "turbo")]
pub mod imp { pub fn go() -> u32 { 1 } }
#[cfg(not(feature = "turbo"))]
pub mod imp { pub fn go() -> u32 { 2 } }
pub fn go() -> u32 { imp::go() }
#[cfg(not(feature = "turbo"))]
pub mod solo { pub fn go() -> u32 { 3 } }
pub fn solo_go() -> u32 { solo::go() }
#[cfg(test)]
mod t { #[test] fn which() { assert_eq!((super::go(), super::solo_go()), (2, 3)); } }
