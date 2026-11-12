#![allow(warnings)]
#[derive(Clone)]
pub struct D(pub u32);
pub struct M(pub u32);
impl Clone for M {
    fn clone(&self) -> M { M(self.0) }
}
pub fn run(d: &D, m: &M) -> u32 {
    let a = D::clone(d);
    let b = Clone::clone(d);
    let c = <D as Clone>::clone(d);
    let e = M::clone(m);
    let f = Clone::clone(m);
    let g = <M as Clone>::clone(m);
    let h = Clone::clone(&1u8);
    a.0 + b.0 + c.0 + e.0 + f.0 + g.0 + h as u32
}
