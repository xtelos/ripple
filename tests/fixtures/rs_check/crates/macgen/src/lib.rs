#[macro_use]
mod gen {
    macro_rules! case {
        ($name:ident, $want:expr) => {
            #[test]
            fn $name() {
                assert_eq!(crate::f(), $want);
            }
        };
    }
}
pub mod sub;
pub mod util;
pub fn f() -> i32 {
    1
}
