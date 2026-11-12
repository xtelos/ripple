#![allow(warnings)]
#[macro_export]
macro_rules! call_helper {
    () => { $crate::helper() };
}
pub fn helper() -> u32 { 1 }
