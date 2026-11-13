
use std::fmt::*;
#[derive(Debug)]
pub struct S;
#[test]
fn std_derive() { assert_eq!(format!("{:?}", S), "S"); }
