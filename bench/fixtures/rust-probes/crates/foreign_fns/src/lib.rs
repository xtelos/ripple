#![allow(warnings)]
extern "C" {
    fn abs(x: i32) -> i32;
    fn labs(x: i64) -> i64;
}
extern "C" fn callback(x: i32) -> i32 { x }
fn abs2(x: i32) -> i32 { x }
pub fn run() -> i32 {
    unsafe { abs(-1) + callback(2) + abs2(3) + labs(-2) as i32 }
}
pub fn shadowed() -> i32 {
    fn abs(x: i32) -> i32 { x + 1 }
    abs(1)
}
#[cfg(test)]
mod proof {
    #[test]
    fn which_runs() {
        assert_eq!(super::shadowed(), 2);
        assert_eq!(super::run(), 1 + 2 + 3 + 2);
    }
}
