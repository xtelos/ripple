#![allow(warnings)]
pub trait T { fn f() -> u32; }

// A: a macro in an inherent impl body generates `f`
pub struct S;
impl T for S { fn f() -> u32 { 1 } }
macro_rules! gen_f { () => { pub fn f() -> u32 { 2 } } }
impl S { gen_f!(); }
pub fn call_a() -> u32 { S::f() }

// B: a module-level macro generates the whole inherent impl
pub struct U;
impl T for U { fn f() -> u32 { 10 } }
macro_rules! gen_impl { ($t:ty) => { impl $t { pub fn f() -> u32 { 20 } } } }
gen_impl!(U);
pub fn call_b() -> u32 { U::f() }

// C: a macro in another module generates the inherent impl
pub struct W;
impl T for W { fn f() -> u32 { 100 } }
mod other {
    macro_rules! g { () => { impl super::W { pub fn f() -> u32 { 200 } } } }
    g!();
}
pub fn call_c() -> u32 { W::f() }

// D: a macro in a trait impl body overrides a default method
pub trait D { fn d() -> u32 { 1000 } }
pub struct X;
macro_rules! gen_d { () => { fn d() -> u32 { 2000 } } }
impl D for X { gen_d!(); }
pub fn call_d1() -> u32 { X::d() }
pub fn call_d2() -> u32 { <X as D>::d() }

// E: a macro generates the blanket impl
pub trait Conv<Y> { fn conv() -> u32; }
pub struct V;
impl Conv<u8> for V { fn conv() -> u32 { 1 } }
macro_rules! blanket { () => { impl<Y> Conv<Y> for Y { fn conv() -> u32 { 2 } } } }
blanket!();
pub fn call_e() -> u32 { <V as Conv<V>>::conv() }

// F: an inherent fn behind a cfg that is off, and a trait impl
pub struct Fc;
impl T for Fc { fn f() -> u32 { 7 } }
#[cfg(feature = "fast")]
impl Fc { pub fn f() -> u32 { 8 } }
pub fn call_f() -> u32 { Fc::f() }

// G: an inherent fn of a specific impl, a trait impl for another argument
pub struct G<Z>(Z);
impl G<u8> { pub fn f() -> u32 { 30 } }
impl T for G<u16> { fn f() -> u32 { 40 } }
pub fn call_g() -> u32 { G::<u16>::f() }

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn which() {
        assert_eq!(call_a(), 2);
        assert_eq!(call_b(), 20);
        assert_eq!(call_c(), 200);
        assert_eq!(call_d1(), 2000);
        assert_eq!(call_d2(), 2000);
        assert_eq!(call_e(), 2);
        assert_eq!(call_f(), 7);
        assert_eq!(call_g(), 40);
    }
}
