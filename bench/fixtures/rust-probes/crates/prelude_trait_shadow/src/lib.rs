#![allow(warnings)]
pub trait Default {
    fn default() -> Self;
}
pub struct S;
impl Default for S {
    fn default() -> S { S }
}
pub trait From<T> {
    fn from(t: T) -> Self;
}
impl From<u8> for S {
    fn from(_t: u8) -> S { S }
}
pub fn run() -> S {
    let a = Default::default();
    let b = From::from(1u8);
    let c = <S as From<u8>>::from(2u8);
    let d = <S as Default>::default();
    let _: S = a;
    let _: S = b;
    let _ = d;
    c
}
