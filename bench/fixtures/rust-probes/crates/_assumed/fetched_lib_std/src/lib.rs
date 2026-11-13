pub fn t_some() -> u32 { Some(1) }
pub fn t_drop() -> u32 { drop(1) }
pub fn t_vec() -> u32 { Vec::new() }
pub fn t_u8() -> u32 { u8::from_str_radix("1", 10) }
#[cfg(test)]
mod tests {
    #[test]
    fn hijacked() { ::core::assert_eq!((super::t_some(), super::t_drop(), super::t_vec(), super::t_u8()), (2, 2, 2, 2)); }
}
