#![allow(warnings)]
use std::fmt::Display;
pub trait Describe {
    fn describe(&self) -> String;
    fn make() -> u32 { 0 }
}
impl<T: Display> Describe for T {
    fn describe(&self) -> String { self.to_string() }
}
pub struct S;
pub trait Conv<X> {
    fn conv() -> u32;
}
impl Conv<u8> for S {
    fn conv() -> u32 { 8 }
}
impl<T> Conv<T> for T {
    fn conv() -> u32 { 1 }
}
pub fn run() -> u32 {
    let a = Describe::describe(&5);
    let b = <i32 as Describe>::describe(&5);
    let c = i32::describe(&5);
    let d = String::describe(&String::new());
    let e = <S as Conv<S>>::conv();
    let f = <S as Conv<u8>>::conv();
    let g = <u8 as Conv<u8>>::conv();
    let h: u32 = <i32 as Describe>::make();
    let _ = (a, b, c, d);
    e + f + g + h
}
