#![allow(warnings)]
pub trait T { fn f() -> u32; fn g() -> u32; }
pub struct Fc;
impl T for Fc { fn f() -> u32 { 7 } fn g() -> u32 { 7 } }
#[cfg(feature = "fast")]
impl Fc { pub fn f() -> u32 { 8 } }
pub struct Fm;
impl T for Fm { fn f() -> u32 { 7 } fn g() -> u32 { 7 } }
impl Fm { #[cfg(feature = "fast")] pub fn g() -> u32 { 8 } }
pub struct Only;
impl Only {
    #[cfg(feature = "fast")] pub fn z() -> u32 { 9 }
    #[cfg(not(feature = "fast"))] pub fn z() -> u32 { 10 }
}
pub struct Solo;
impl Solo { #[cfg(not(feature = "fast"))] pub fn only() -> u32 { 11 } }
pub fn a() -> u32 { Fc::f() }
pub fn b() -> u32 { Fm::g() }
pub fn c() -> u32 { Only::z() }
pub fn d() -> u32 { Solo::only() }
pub trait D { fn d() -> u32 { 1 } }
pub struct Dm;
impl D for Dm { #[cfg(feature = "fast")] fn d() -> u32 { 2 } }
pub fn e() -> u32 { Dm::d() }
#[cfg(test)]
mod t { #[test] fn which() { assert_eq!((super::a(), super::b(), super::c(), super::d(), super::e()), (7, 7, 10, 11, 1)); } }
