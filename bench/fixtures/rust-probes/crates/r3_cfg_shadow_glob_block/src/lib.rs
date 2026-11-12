#![allow(warnings)]
pub mod lib2 { pub fn helper() -> u32 { 10 } }
pub mod user {
    use crate::lib2::*;
    #[cfg(feature = "shim")]
    fn helper() -> u32 { 20 }
    pub fn run() -> u32 { helper() }
    pub fn blockrun() -> u32 {
        #[cfg(feature = "shim")]
        fn helper() -> u32 { 30 }
        helper()
    }
}
#[cfg(test)]
mod t { #[test] fn which() { assert_eq!((super::user::run(), super::user::blockrun()), (10, 10)); } }
