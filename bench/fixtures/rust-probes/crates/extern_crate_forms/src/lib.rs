#![allow(warnings)]
extern crate self as me;
extern crate alloc;
extern crate core as c;
extern crate std as s;
pub fn helper() -> u32 { 7 }
pub fn run() -> u32 {
    me::helper() + c::cmp::max(1, 2) + alloc::vec::Vec::<u8>::new().len() as u32 + s::cmp::min(1, 2) + crate::helper()
}
pub mod sub {
    pub fn run() -> u32 { me::helper() + super::helper() + c::cmp::max(2, 3) }
}
