#![allow(warnings)]
mod other {
    pub fn helper() -> u32 { 100 }
}
use other::*;
fn helper() -> u32 { 1 }
#[tokio::main(flavor = "current_thread")]
pub async fn run() -> u32 {
    helper()
}
#[cfg(test)]
mod t {
    use super::helper;

    #[tokio::test(flavor = "current_thread")]
    async fn current_thread() {
        assert_eq!(helper(), 1);
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn multi_thread() {
        assert_eq!(helper(), 1);
    }

    #[tokio::test(start_paused = true)]
    async fn paused() {
        assert_eq!(helper(), 1);
    }

    #[tokio::test(crate = "tokio")]
    async fn crate_named() {
        assert_eq!(helper(), 1);
    }

    #[test]
    fn main_with_args() {
        assert_eq!(super::run(), 1);
    }
}
