#![allow(warnings)]
mod other {
    pub fn helper() -> u32 { 100 }
}
use other::*;
fn helper() -> u32 { 1 }
#[tokio::main]
pub async fn run() -> u32 {
    helper()
}
#[cfg(test)]
mod t {
    use super::helper;

    #[tokio::test]
    async fn inherited_test() {
        assert_eq!(helper(), 1);
    }

    #[test]
    fn inherited_main() {
        assert_eq!(super::run(), 1);
    }
}
