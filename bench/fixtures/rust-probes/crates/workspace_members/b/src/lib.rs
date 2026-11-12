#![allow(warnings)]
pub fn f() -> u32 { 1 }
pub mod sub {
    pub fn g() -> u32 { 2 }
}
pub struct Thing(pub u32);
impl Thing {
    pub fn new() -> Thing { Thing(Self::make()) }
    pub fn make() -> u32 { 3 }
}
