#![allow(warnings)]
pub trait T { fn f() -> u32; }

// A: a private inherent fn, and a trait impl of the same name, called from outside the module
pub mod m {
    pub struct P;
    impl P { fn f() -> u32 { 1 } }
    impl super::T for P { fn f() -> u32 { 2 } }
}
use m::P;
pub fn call_a() -> u32 { P::f() }

// B: a module-level item behind an off cfg shadows a glob
pub mod lib2 { pub fn helper() -> u32 { 10 } }
pub mod user {
    use crate::lib2::*;
    #[cfg(feature = "shim")]
    fn helper() -> u32 { 20 }
    pub fn run() -> u32 { helper() }
}

// C: an inherent assoc const of fn type, and a trait fn of the same name
pub struct C;
fn seven() -> u32 { 7 }
impl C { const f: fn() -> u32 = seven; }
impl T for C { fn f() -> u32 { 8 } }
pub fn call_c() -> u32 { C::f() }

// D: raw identifiers that name the same thing as plain ones
fn helper() -> u32 { 100 }
pub fn raw_local() -> u32 { let helper = || 5u32; r#helper() }
pub fn raw_block() -> u32 { fn r#helper() -> u32 { 6 } helper() }

// E: `Self::f` in a trait impl for G<u16> while the inherent f is G<u8>'s
pub struct G<Z>(Z);
impl G<u8> { pub fn f() -> u32 { 30 } }
impl T for G<u16> { fn f() -> u32 { 40 } }
pub trait Twice { fn twice() -> u32; }
impl Twice for G<u16> { fn twice() -> u32 { Self::f() * 2 } }
type G16 = G<u16>;
pub fn call_e() -> u32 { G16::f() }

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn which() {
        assert_eq!(call_a(), 2);
        assert_eq!(user::run(), 10);
        assert_eq!(call_c(), 7);
        assert_eq!(raw_local(), 5);
        assert_eq!(raw_block(), 6);
        assert_eq!(<G<u16> as Twice>::twice(), 80);
        assert_eq!(call_e(), 40);
    }
}
