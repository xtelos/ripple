#![allow(warnings)]
pub trait Make { fn make() -> u32; }
#[macro_export]
macro_rules! impl_make { ($t:ty, $n:expr) => { impl $crate::Make for $t { fn make() -> u32 { $n } } } }
