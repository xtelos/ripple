#![allow(warnings)]
use b::sub::g;
pub fn run() -> u32 {
    b::f() + g() + b::Thing::new().0 + b::Thing::make()
}
