mod other { pub fn helper() -> u32 { 1 } }
pub mod c {
    use super::other::*;
    #[test]
    fn t() {}
    pub fn two() -> u32 { helper() }
}
#[::core::prelude::v1::test]
fn other_attr() { ::core::assert_eq!(c::two(), 2); }
