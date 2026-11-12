#![allow(warnings)]
fn private_helper() -> u32 { 1 }
pub fn public() -> u32 { private_helper() }
pub mod inner {
    fn secret() -> u32 { 2 }
    pub fn run() -> u32 { secret() }
}
#[cfg(test)]
mod tests {
    use super::*;
    use super::inner::run;
    #[test]
    fn t() {
        assert_eq!(private_helper(), 1);
        assert_eq!(public(), 1);
        assert_eq!(run(), 2);
    }
}
