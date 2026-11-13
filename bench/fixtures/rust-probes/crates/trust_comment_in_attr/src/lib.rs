#![allow(warnings)]
mod other {
    pub fn helper() -> u32 { 100 }
}
use other::*;
fn helper() -> u32 { 1 }
#[cfg(test)]
mod t {
    use super::helper;
    use serial_test /* c */ :: // d
        serial;

    #[tokio /* c */ :: test]
    async fn block_comment() {
        assert_eq!(helper(), 1);
    }

    #[tokio:: // c
        test]
    async fn line_comment() {
        assert_eq!(helper(), 1);
    }

    # /* c */ [ /* d */ tokio::test /* e */ ]
    async fn around_the_brackets() {
        assert_eq!(helper(), 1);
    }

    #[test]
    #[serial_test /* c */ :: serial]
    fn serial_path() {
        assert_eq!(helper(), 1);
    }

    #[test]
    #[serial(/* inner_attrs = [x] */ key)]
    fn serial_args() {
        assert_eq!(helper(), 1);
    }
}
