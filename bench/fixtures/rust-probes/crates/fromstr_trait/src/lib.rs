#![allow(warnings)]
use std::str::FromStr;
pub struct Num(pub u32);
impl FromStr for Num {
    type Err = ();
    fn from_str(_s: &str) -> Result<Num, ()> { Ok(Num(1)) }
}
pub struct Other(pub u32);
impl FromStr for Other {
    type Err = ();
    fn from_str(_s: &str) -> Result<Other, ()> { Ok(Other(2)) }
}
pub fn run() -> u32 {
    let a = Num::from_str("1").unwrap();
    let b: Num = FromStr::from_str("2").unwrap();
    let c = <Num as FromStr>::from_str("3").unwrap();
    let d = u32::from_str("4").unwrap();
    let e: Other = std::str::FromStr::from_str("5").unwrap();
    let f = "6".parse::<Num>().unwrap();
    a.0 + b.0 + c.0 + d + e.0 + f.0
}
