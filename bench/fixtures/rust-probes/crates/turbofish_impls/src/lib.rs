#![allow(warnings)]
pub struct W<T>(pub T);
impl<T> W<T> {
    pub fn new(t: T) -> W<T> { W(t) }
}
impl W<u8> {
    pub fn make() -> u32 { 8 }
}
impl W<u16> {
    pub fn make() -> u32 { 16 }
}
impl W<u8> {
    pub fn only8() -> u32 { 1 }
}
pub fn run() -> u32 {
    let _ = W::<u8>::new(1);
    let _ = W::new(2u16);
    W::<u8>::make() + W::<u16>::make() + W::<u8>::only8()
}
