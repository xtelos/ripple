#![allow(warnings)]
mod x {
    pub fn f() -> u32 { 1 }
    pub fn g() -> u32 { 3 }
}
macro_rules! bind {
    ($n:ident) => {
        let $n = || 2u32;
    };
}
macro_rules! noop {
    () => {};
}
pub fn run() -> u32 {
    use crate::x::f;
    bind!(f);
    f()
}
pub fn inner_block() -> u32 {
    use crate::x::g;
    let a = { bind!(g); g() };
    a + g()
}
pub fn noop_only() -> u32 {
    use crate::x::f;
    noop!();
    f()
}
#[cfg(test)]
mod proof {
    #[test]
    fn which_runs() {
        assert_eq!(super::run(), 2);
        assert_eq!(super::inner_block(), 2 + 3);
        assert_eq!(super::noop_only(), 1);
    }
}
