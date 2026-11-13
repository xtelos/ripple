extern crate fakestd as std;
mod other { pub fn one() -> u32 { 1 } }
pub mod c {
    use super::other::*;
    #[derive(Debug)]
    pub struct S;
    pub fn two() -> u32 { one() }
}
#[test]
fn std_derive() { assert_eq!(format!("{:?}", c::S), "S"); assert_eq!(c::two(), 1); }
