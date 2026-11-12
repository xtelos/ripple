#![allow(warnings)]
mod util { pub fn helper() -> u32 { 1 } }
pub mod api {
    mod util { pub fn helper() -> u32 { 2 } }
    pub use util::helper;
    pub fn local() -> u32 { util::helper() }
}
pub fn root() -> u32 { util::helper() + api::local() + api::helper() }
#[cfg(test)]
mod t { #[test] fn which() { assert_eq!(super::root(), 1 + 2 + 1); } }
