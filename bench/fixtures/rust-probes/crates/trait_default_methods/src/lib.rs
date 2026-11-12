#![allow(warnings)]
pub trait Shape {
    fn area(&self) -> u32;
    fn name() -> u32 { 1 }
    fn describe(&self) -> u32 { self.area() + Self::name() + Self::extra() }
    fn extra() -> u32 { 5 }
}
pub struct Sq;
pub struct Ci;
impl Shape for Sq {
    fn area(&self) -> u32 { 4 }
    fn name() -> u32 { 2 }
}
impl Shape for Ci {
    fn area(&self) -> u32 { 3 }
}
pub fn run() -> u32 {
    Sq::name() + Ci::name() + Sq::extra() + <Sq as Shape>::name() + Shape::area(&Ci) + <Ci as Shape>::extra()
}
