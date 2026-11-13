#![allow(warnings)]
fn helper() -> u32 { 1 }
// the published macro, as written, in a repo that has a package of its own called tokio-macros (not patched in): ripple
// refuses it all the same, since it cannot tell a member that is unrelated from one that stands in for the macro crate
#[tokio::main]
pub async fn run() -> u32 {
    helper()
}
#[cfg(test)]
mod t {
    #[test]
    fn which() {
        assert_eq!(super::run(), 1);
    }
}
