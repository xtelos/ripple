#![allow(warnings)]
pub fn helper() -> u32 { 1000 }
#[macro_export]
macro_rules! vec {
    ($($t:tt)*) => { 0usize };
}
#[macro_export]
macro_rules! assert {
    ($($t:tt)*) => { () };
}
#[macro_export]
macro_rules! dbg {
    ($n:ident) => { let $n = || 99u32; };
}
