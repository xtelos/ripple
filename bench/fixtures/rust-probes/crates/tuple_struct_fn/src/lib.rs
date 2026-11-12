#![allow(warnings)]
mod m {
    pub struct P(pub u32);
    pub fn P2() -> u32 { 2 }
}
pub struct Q(pub u32);
fn Q2() -> u32 { 1 }
pub fn run() -> u32 {
    let a = m::P(1);
    let b = Q(2);
    let c = m::P2();
    let d = Q2();
    a.0 + b.0 + c + d
}
mod shadow {
    pub fn Q(x: u32) -> u32 { x }
    pub fn run() -> u32 { Q(1) }
}
mod shadow_glob {
    use crate::*;
    pub fn run() -> u32 { Q(1).0 }
}
