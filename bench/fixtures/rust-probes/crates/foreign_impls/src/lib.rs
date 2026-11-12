#![allow(warnings)]
pub trait Ext {
    fn first_or(&self, d: u8) -> u8;
    fn new() -> Self;
    fn len2(&self) -> usize { 0 }
}
impl Ext for Vec<u8> {
    fn first_or(&self, d: u8) -> u8 { d }
    fn new() -> Self { Vec::new() }
}
pub fn run() -> u8 {
    let v: Vec<u8> = Vec::new();
    let w: Vec<u8> = <Vec<u8> as Ext>::new();
    let x: Vec<u8> = Ext::new();
    let _ = (w, x);
    Ext::first_or(&v, 1) + <Vec<u8> as Ext>::first_or(&v, 2) + Vec::<u8>::first_or(&v, 3)
}
