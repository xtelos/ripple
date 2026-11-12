#![allow(warnings)]
fn helper() -> u32 { 2 }
macro_rules! local_call {
    () => { $crate::helper() };
}
pub fn run() -> u32 { mac::call_helper!() + mac::helper() }
pub fn run2() -> u32 { local_call!() + helper() }
