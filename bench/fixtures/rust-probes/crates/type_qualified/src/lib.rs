#![allow(warnings)]
pub trait T1 {
    fn same() -> u32;
    fn only1() -> u32 { 10 }
}
pub trait T2 {
    fn same() -> u32;
}
pub struct Two;
impl T1 for Two { fn same() -> u32 { 1 } }
impl T2 for Two { fn same() -> u32 { 2 } }
pub struct Inh;
impl Inh {
    pub fn same() -> u32 { 7 }
}
impl T1 for Inh { fn same() -> u32 { 8 } }
impl T2 for Inh { fn same() -> u32 { 9 } }
pub fn run() -> u32 {
    <Two as T1>::same() + <Two as T2>::same() + Inh::same() + <Inh as T1>::same() + <Inh as T2>::same()
        + Two::only1() + <Two as T1>::only1() + <Inh>::same()
}
