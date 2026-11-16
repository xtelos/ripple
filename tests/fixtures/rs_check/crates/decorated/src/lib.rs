/// plain
///
/// ```
/// assert_eq!(1, 1);
/// ```
///
/// ```compile_fail
/// let x: i32 = "s";
/// ```
///
/// ```no_run
/// loop {}
/// ```
///
/// ```should_panic
/// panic!("doc boom");
/// ```
///
/// ```ignore
/// not rust
/// ```
///
/// ```should_panic
/// assert_eq!(1, 1);
/// ```
pub fn f() {}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    #[should_panic]
    fn sp() {
        panic!("boom");
    }
    #[test]
    #[should_panic(expected = "boom")]
    fn sp_msg() {
        panic!("boom here");
    }
    #[test]
    #[should_panic(expected = "boom")]
    fn sp_wrong_msg() {
        panic!("other");
    }
    #[test]
    #[should_panic]
    fn sp_no_panic() {}
    #[test]
    #[ignore]
    #[should_panic]
    fn sp_ignored() {
        panic!("x");
    }
    #[test]
    #[ignore = "needs a database"]
    fn ign_reason() {}
    #[test]
    fn r#match() {}
    #[test]
    fn plain() {}
}
