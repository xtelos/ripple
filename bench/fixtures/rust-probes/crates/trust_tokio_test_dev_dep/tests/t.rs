#![allow(warnings)]
mod other {
    pub fn helper() -> u32 { 100 }
}
use other::*;
use trust_tokio_test_dev_dep::helper;

#[tokio::test]
async fn which() {
    assert_eq!(helper(), 1);
}
