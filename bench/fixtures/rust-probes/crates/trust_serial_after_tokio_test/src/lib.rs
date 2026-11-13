#![allow(warnings)]
mod util {
    pub fn _u_internal() -> u8 { 1 }
}
use util::*;
use serial_test::serial;

// serial_test_derive 0.10 to 3.1 puts `async fn _u_internal` beside an async function it is written on, which would win
// over the glob import below. Here tokio::test, written first, has already made the function a plain one, so serial
// sees no `async` and adds nothing: the call below is the util one. ripple does not read the order of the attributes,
// so it refuses an `async fn` under serial, and leaves the call unresolved.
#[tokio::test]
#[serial]
async fn u() {}

pub fn other() -> &'static str {
    std::any::type_name_of_val(&_u_internal())
}

#[cfg(test)]
mod t {
    #[test]
    fn which() {
        assert_eq!(super::other(), "u8");
    }
}
