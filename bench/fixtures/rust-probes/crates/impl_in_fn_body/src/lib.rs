#![allow(warnings)]
pub struct S;
impl S {
    pub fn new() -> u32 { 1 }
}
pub fn run() -> u32 {
    struct S;
    impl S {
        fn new() -> u32 { 2 }
        fn twice() -> u32 { Self::new() * 2 }
    }
    S::new() + S::twice()
}
pub fn other() -> u32 { S::new() }
#[cfg(test)]
mod proof {
    #[test]
    fn which_runs() {
        assert_eq!(super::run(), 2 + 4);
        assert_eq!(super::other(), 1);
    }
}
