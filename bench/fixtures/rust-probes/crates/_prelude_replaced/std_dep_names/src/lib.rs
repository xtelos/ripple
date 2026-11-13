pub struct S;
pub fn t_some() -> u32 { Some(1) }
pub fn t_drop() -> u32 { drop(1) }
pub fn t_vec() -> u32 { Vec::new() }
pub fn t_default() -> u32 { let x: u32 = Default::default(); x }
pub fn t_u8() -> u32 { u8::from_str_radix("1", 10) }
pub fn t_to_string() -> u32 { S::to_string(&S) }
#[test]
fn hijacked() {
    ::core::assert_eq!((t_some(), t_drop(), t_vec(), t_default(), t_u8(), t_to_string()), (2, 2, 2, 2, 2, 2));
}
