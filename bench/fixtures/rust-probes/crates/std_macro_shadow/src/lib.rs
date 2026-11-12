#![allow(warnings)]
fn other() -> u32 { 1 }
mod m1 {
    macro_rules! format {
        ($($t:tt)*) => { crate::helper() };
    }
    pub fn run() -> u32 { format!("{}", super::other()) }
}
fn helper() -> u32 { 9 }
mod early {
    pub fn run() -> String { format!("{}", super::other()) }
}
mod mac {
    macro_rules! shout {
        ($e:expr) => { 5 };
    }
    pub(crate) use shout as assert_eq;
}
mod m2 {
    use crate::mac::assert_eq;
    pub fn run() -> u32 { assert_eq!(super::other()) }
}
macro_rules! println {
    ($($t:tt)*) => { helper() };
}
pub fn after() -> u32 { println!("{}", other()) }
