#![allow(warnings)]
mod parent {
    pub mod child {
        pub fn pubf() -> u32 { 1 }
        pub(crate) fn crate_f() -> u32 { 2 }
        pub(super) fn super_f() -> u32 { 3 }
        pub(in crate::parent) fn in_f() -> u32 { 4 }
        fn private_f() -> u32 { 5 }
        pub fn private_visible_to_self() -> u32 { private_f() }
    }
    use child::*;
    fn private_f() -> u32 { 50 }
    pub fn run() -> u32 { pubf() + crate_f() + super_f() + in_f() + private_f() }
    pub fn run_block() -> u32 {
        use self::child::*;
        // child's private_f is not visible here, so the module's own is the one
        private_f() + super_f()
    }
}
mod other {
    use crate::parent::child::*;
    pub fn run() -> u32 { pubf() + crate_f() }
}
