#![allow(warnings)]
fn helper() -> u32 { 100 }
pub fn raw_local() -> u32 { let helper = || 5u32; r#helper() }
pub fn raw_block() -> u32 { fn r#helper() -> u32 { 6 } helper() }
fn r#match() -> u32 { 7 }
pub fn call_match() -> u32 { r#match() }
pub mod r#type { pub fn r#fn() -> u32 { 8 } }
pub fn call_mod() -> u32 { r#type::r#fn() }
pub struct r#struct;
impl r#struct { pub fn r#new() -> u32 { 9 } }
pub fn call_struct() -> u32 { r#struct::new() }
use self::r#type::r#fn as r#try;
pub fn call_try() -> u32 { r#try() }
#[cfg(test)]
mod t {
    #[test]
    fn which() {
        assert_eq!((super::raw_local(), super::raw_block(), super::call_match(), super::call_mod(), super::call_struct(), super::call_try()), (5, 6, 7, 8, 9, 8));
    }
}
