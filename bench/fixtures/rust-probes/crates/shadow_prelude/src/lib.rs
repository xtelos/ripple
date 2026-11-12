#![allow(warnings)]
pub fn drop<T>(_x: T) {}
pub struct Vec;
impl Vec {
    pub fn new() -> u32 { 1 }
}
mod user {
    use super::*;
    pub fn run() -> u32 {
        drop(1);
        Vec::new()
    }
}
mod user2 {
    pub fn run() {
        drop(1);
        let _: std::vec::Vec<u8> = std::vec::Vec::new();
        let _ = String::new();
        let _ = Box::new(1);
    }
}
pub fn root_run() -> u32 {
    let _ = std::vec::Vec::<u8>::new();
    Vec::new()
}
