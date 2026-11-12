#![allow(warnings)]
#[inline]
fn a() -> u32 { 1 }
#[must_use]
#[allow(dead_code)]
#[doc = "x"]
#[deprecated]
pub fn b() -> u32 { a() }
#[derive(Clone, Debug, Default, PartialEq)]
pub struct S;
impl S {
    #[inline(always)]
    pub fn make() -> S { S }
    #[track_caller]
    pub fn go() -> u32 { let _ = Self::make(); 5 }
}
#[repr(C)]
pub struct R(u8);
pub fn use_it() -> u32 { let _ = S::make(); S::go() + a() }
#[cfg(test)]
mod tests {
    #[test]
    #[ignore = "no"]
    fn ignored() { assert_eq!(super::b(), 1); }
    #[test]
    fn run() { assert_eq!(super::use_it(), 6); }
}
