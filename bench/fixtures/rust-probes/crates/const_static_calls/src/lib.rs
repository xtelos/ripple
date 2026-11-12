#![allow(warnings)]
const fn two() -> u32 { 2 }
fn three() -> u32 { 3 }
const X: u32 = two() + 1;
static Y: std::sync::OnceLock<u32> = std::sync::OnceLock::new();
pub enum E {
    A = two() as isize,
}
pub struct S;
impl S {
    const C: u32 = two();
    pub fn get() -> u32 { Self::C + X + three() }
}
pub fn run() -> u32 { *Y.get_or_init(three) + S::get() }
