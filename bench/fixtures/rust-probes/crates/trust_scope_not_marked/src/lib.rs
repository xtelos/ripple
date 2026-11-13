#![allow(warnings)]
pub fn helper() -> u32 { 1 }
#[cfg(test)]
mod t {
    use super::*;

    #[tokio::test]
    async fn trusted() {
        assert_eq!(helper(), 1);
    }

    fn found_only_through_the_glob() -> u32 {
        helper()
    }

    #[test]
    fn plain() {
        assert_eq!(found_only_through_the_glob(), 1);
    }
}
