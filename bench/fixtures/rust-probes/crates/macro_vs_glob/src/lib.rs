#![allow(warnings)]
mod a {
    pub fn f() -> u32 { 1 }
    pub fn g() -> u32 { 5 }
}
pub mod b {
    use crate::a::*;
    macro_rules! make {
        ($n:ident) => {
            fn $n() -> u32 { 2 }
        };
    }
    make!(f);
    pub fn run() -> u32 { f() }
    pub fn run_g() -> u32 { g() }
}
mod c {
    macro_rules! make {
        ($n:ident) => {
            pub fn $n() -> u32 { 3 }
        };
    }
    make!(drop2);
    pub fn real() -> u32 { 4 }
}
pub mod d {
    use crate::c::*;
    pub fn run() -> u32 { drop2() + real() }
    pub fn run_prelude() { drop(1); }
}
pub mod e {
    use crate::c::*;
    macro_rules! other { () => {}; }
    other!();
    pub fn run() { drop(2); }
}
#[test]
fn which() {
    assert_eq!(b::run(), 2);
}
