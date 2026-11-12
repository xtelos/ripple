mod shared;
fn local() -> u32 { 2 }
fn main() {
    println!("{}", shared::go() + local());
}
#[cfg(test)]
mod proof { #[test] fn t() { assert_eq!(super::local(), 2); assert_eq!(super::shared::go(), 2); } }
