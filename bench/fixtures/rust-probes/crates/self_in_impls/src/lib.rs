#![allow(warnings)]
pub struct S;
impl S {
    pub fn helper() -> u32 { 1 }
    pub fn a() -> u32 { Self::helper() + Self::b() + S::helper() }
}
impl S {
    pub fn b() -> u32 { 2 }
    pub fn c() -> u32 { Self::a() }
}
pub trait T {
    fn req() -> u32;
    fn dflt() -> u32 { Self::req() + 1 }
}
impl T for S {
    fn req() -> u32 { Self::helper() }
}
pub struct G<X>(pub X);
impl<X> G<X> {
    pub fn mk() -> u32 { 5 }
    pub fn run() -> u32 { Self::mk() + G::<u8>::mk() }
}
