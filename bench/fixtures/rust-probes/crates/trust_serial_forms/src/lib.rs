#![allow(warnings)]
mod other {
    pub fn helper() -> u32 { 100 }
}
use other::*;
fn helper() -> u32 { 1 }
#[cfg(test)]
mod t {
    use super::helper;
    use serial_test::serial;

    #[test]
    #[serial]
    fn plain() {
        assert_eq!(helper(), 1);
    }

    #[test]
    #[serial(key)]
    fn keyed() {
        assert_eq!(helper(), 1);
    }

    #[test]
    #[serial(one, two)]
    fn two_keys() {
        assert_eq!(helper(), 1);
    }

    #[test]
    #[serial_test::serial]
    fn by_path() {
        assert_eq!(helper(), 1);
    }

    #[test]
    #[cfg_attr(test, serial)]
    fn under_cfg_attr() {
        assert_eq!(helper(), 1);
    }
}
