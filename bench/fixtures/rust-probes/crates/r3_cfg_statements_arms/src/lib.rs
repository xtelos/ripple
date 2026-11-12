#![allow(warnings)]
fn helper() -> u32 { 1 }
pub fn stmt() -> u32 {
    #[cfg(feature = "x")]
    let helper = || 5u32;
    helper()
}
pub fn arm(v: Option<u32>) -> u32 {
    match v {
        #[cfg(feature = "x")]
        Some(0) => { let helper = || 9u32; helper() }
        _ => helper(),
    }
}
#[cfg(test)]
mod t { #[test] fn which() { assert_eq!((super::stmt(), super::arm(Some(0))), (1, 1)); } }
