#![allow(warnings)]
pub trait Tr {
    fn f() -> u32;
    fn g() -> u32 { 1 }
}
pub struct A;
impl Tr for A {
    fn f() -> u32 { 1 }
}
pub struct B;
impl Tr for B {
    fn f() -> u32 { 2 }
    fn g() -> u32 { 3 }
}
pub fn run<T: Tr>() -> u32 { T::f() + <T as Tr>::f() + T::g() }
pub fn run_where<T>() -> u32 where T: Tr + Default {
    let _ = T::default();
    T::f()
}
pub struct W<T>(pub T);
impl<T: Tr> W<T> {
    pub fn h() -> u32 { T::f() + Self::k() }
    pub fn k() -> u32 { 0 }
}
pub fn calls() -> u32 { run::<A>() + run::<B>() + W::<A>::h() }
