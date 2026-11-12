#![allow(warnings)]
use std::convert::TryFrom;
pub struct Small(pub u8);
impl TryFrom<u32> for Small {
    type Error = ();
    fn try_from(v: u32) -> Result<Small, ()> { if v < 256 { Ok(Small(v as u8)) } else { Err(()) } }
}
pub fn run() -> u8 {
    let a = Small::try_from(5u32).unwrap();
    let b = <Small as TryFrom<u32>>::try_from(6u32).unwrap();
    let c: Small = TryFrom::try_from(7u32).unwrap();
    let d = u8::try_from(300i32).unwrap_or(0);
    let e: Result<Small, _> = 9u32.try_into();
    let _ = e;
    a.0 + b.0 + c.0 + d
}
