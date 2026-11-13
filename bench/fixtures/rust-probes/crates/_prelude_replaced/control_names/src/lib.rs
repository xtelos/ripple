pub struct S;
impl std::fmt::Display for S {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result { write!(f, "s") }
}
pub fn t_some() -> Option<u32> { Some(1) }
pub fn t_drop() { drop(1) }
pub fn t_vec() -> Vec<u32> { Vec::new() }
pub fn t_default() -> u32 { let x: u32 = Default::default(); x }
pub fn t_u8() -> Result<u8, std::num::ParseIntError> { u8::from_str_radix("1", 10) }
pub fn t_to_string() -> String { S::to_string(&S) }
#[test]
fn std_names() {
    assert_eq!((t_some(), t_default(), t_u8().unwrap(), t_to_string().as_str()), (Some(1), 0, 1, "s"));
    t_drop();
    assert!(t_vec().is_empty());
}
