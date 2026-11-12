#![allow(warnings)]
pub trait Conv<X> {
    fn conv(&self) -> u32;
    fn make() -> u32 { 0 }
}
pub struct S;
impl Conv<u8> for S {
    fn conv(&self) -> u32 { 8 }
}
impl Conv<u16> for S {
    fn conv(&self) -> u32 { 16 }
    fn make() -> u32 { 1 }
}
pub fn run(s: &S) -> u32 {
    <S as Conv<u8>>::conv(s)
        + <S as Conv<u16>>::conv(s)
        + <S as Conv<u16>>::make()
        + <S as Conv<u8>>::make()
        + Conv::<u8>::conv(s)
}
