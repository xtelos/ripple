#![allow(warnings)]
#[cfg(unix)]
pub fn plat() -> u32 { 1 }
#[cfg(not(unix))]
pub fn plat() -> u32 { 2 }
pub struct S;
impl S {
    #[cfg(unix)]
    pub fn p() -> u32 { 1 }
    #[cfg(not(unix))]
    pub fn p() -> u32 { 2 }
    pub fn q() -> u32 { Self::p() }
}
#[cfg(unix)]
impl S {
    pub fn only() -> u32 { 1 }
}
#[cfg(not(unix))]
impl S {
    pub fn only() -> u32 { 2 }
}
#[cfg(unix)]
mod imp {
    pub fn go() -> u32 { 1 }
}
#[cfg(not(unix))]
mod imp {
    pub fn go() -> u32 { 2 }
}
#[cfg(feature = "nope")]
pub fn gated() -> u32 { 3 }
pub fn run() -> u32 { plat() + S::p() + imp::go() + S::only() + S::q() }
