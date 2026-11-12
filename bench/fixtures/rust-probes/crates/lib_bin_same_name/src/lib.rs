#![allow(warnings)]
pub fn f() -> u32 { 1 }
pub mod util {
    pub fn u() -> u32 { crate::f() }
}
#[cfg(test)]
mod proof {
    #[test]
    fn which_runs() {
        assert_eq!(super::util::u(), 1);
    }
}
