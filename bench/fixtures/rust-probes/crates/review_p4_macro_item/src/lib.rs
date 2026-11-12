mod a {
    pub fn f() -> u32 {
        1
    }
}
pub mod b {
    use crate::a::*;
    macro_rules! make {
        ($n:ident) => {
            fn $n() -> u32 {
                2
            }
        };
    }
    make!(f);
    pub fn run() -> u32 {
        f()
    }
}
#[test]
fn which() {
    assert_eq!(b::run(), 2);
}
