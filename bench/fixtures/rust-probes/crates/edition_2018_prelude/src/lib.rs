#![allow(warnings)]
// TryFrom is not in the edition 2018 prelude, so a repo trait of the name does not collide with it
pub trait TryFrom<T>: Sized {
    fn try_from(t: T) -> Result<Self, ()>;
}
pub struct S;
impl TryFrom<u8> for S {
    fn try_from(_t: u8) -> Result<S, ()> { Ok(S) }
}
pub fn run() -> Result<S, ()> {
    let a = S::try_from(1u8);
    let b: Result<S, ()> = TryFrom::try_from(2u8);
    let c = <S as TryFrom<u8>>::try_from(3u8);
    a.and(b).and(c)
}
