#![allow(warnings)]
pub trait T { fn f() -> u32; }
pub struct G<Z>(Z);
impl G<u8> { pub fn f() -> u32 { 30 } }
impl T for G<u16> { fn f() -> u32 { 40 } }
impl<Z> G<Z> { pub fn bare() -> u32 { 1 } }
pub trait Twice { fn twice() -> u32; }
impl Twice for G<u16> { fn twice() -> u32 { Self::f() * 2 } }
impl G<u8> { pub fn twice8() -> u32 { Self::f() * 2 } }
type G16 = G<u16>;
type G8 = G<u8>;
pub fn t16() -> u32 { G::<u16>::f() }
pub fn t8() -> u32 { G::<u8>::f() }
pub fn a16() -> u32 { G16::f() }
pub fn a8() -> u32 { G8::f() }
pub fn bare() -> u32 { G::<u64>::bare() + G::<bool>::bare() }
#[cfg(test)]
mod t {
    use super::*;
    #[test]
    fn which() {
        assert_eq!((t16(), t8(), a16(), a8(), bare()), (40, 30, 40, 30, 2));
        assert_eq!(<G<u16> as Twice>::twice(), 80);
        assert_eq!(G::<u8>::twice8(), 60);
    }
}
