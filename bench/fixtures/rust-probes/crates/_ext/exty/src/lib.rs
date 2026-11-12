#![allow(warnings)]
pub fn helper() -> u32 { 1000 }
pub fn extra() -> u32 { 1 }
#[macro_export]
macro_rules! format {
    ($($t:tt)*) => { 0usize };
}
#[macro_export]
macro_rules! vec {
    ($($t:tt)*) => { 0usize };
}
