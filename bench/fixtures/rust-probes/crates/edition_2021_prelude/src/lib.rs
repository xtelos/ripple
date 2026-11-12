#![allow(warnings)]
pub struct S;
pub fn narrow(x: u32) -> u8 { u8::try_from(x).unwrap() }
pub fn wide(x: u8) -> u32 { u32::try_from(x).unwrap() }
pub fn convert(x: u32) -> Result<u8, std::num::TryFromIntError> { TryFrom::try_from(x) }
pub fn collect() -> Vec<u8> { FromIterator::from_iter(vec![1u8]) }
