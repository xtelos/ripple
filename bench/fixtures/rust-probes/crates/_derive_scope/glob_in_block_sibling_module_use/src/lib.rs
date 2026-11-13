
use genmacros::Debug;
fn f() -> String {
    use std::fmt::*;
    #[derive(Debug)]
    struct T;
    format!("{:?}", T)
}
