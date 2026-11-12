#![allow(warnings)]
macro_rules! mkfn {
    ($n:ident, $v:expr) => {
        fn $n() -> u32 { $v }
    };
}
macro_rules! mkmod {
    ($m:ident) => {
        mod $m {
            pub fn inner() -> u32 { 1 }
        }
    };
}
macro_rules! mklocal {
    ($n:ident) => {
        let $n = || 99u32;
    };
}
mkfn!(made, 5);
mkmod!(gen);
fn plain() -> u32 { 2 }
fn lookup() -> u32 { 3 }
pub fn run() -> u32 { made() + gen::inner() + plain() }
pub fn local() -> u32 {
    mklocal!(plain);
    plain()
}
pub fn also_item() -> u32 {
    mkfn!(plain, 8);
    plain()
}
pub fn untouched() -> u32 { lookup() }
#[cfg(test)]
mod proof {
    #[test]
    fn which_runs() {
        assert_eq!(super::run(), 5 + 1 + 2);
        assert_eq!(super::local(), 99);
        assert_eq!(super::also_item(), 8);
        assert_eq!(super::untouched(), 3);
    }
}
