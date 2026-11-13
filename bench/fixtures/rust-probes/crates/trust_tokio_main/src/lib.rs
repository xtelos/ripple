#![allow(warnings)]
mod util {
    pub fn helper() -> u32 { 100 }
}
use util::*;
fn helper() -> u32 { 1 }
#[tokio::main]
pub async fn run() -> u32 {
    helper()
}
pub fn caller() -> u32 {
    run()
}
#[cfg(test)]
mod t {
    #[test]
    fn which() {
        assert_eq!((super::run(), super::caller()), (1, 1));
    }
}
