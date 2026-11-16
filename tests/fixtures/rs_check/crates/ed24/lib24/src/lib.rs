/// ```
/// assert_eq!(lib24::f(), 1);
/// ```
pub fn f0() {}

/// ```should_panic
/// panic!("expected");
/// ```
pub fn f1() {}

/// ```no_run
/// loop {}
/// ```
pub fn f2() {}

/// ```compile_fail
/// let x: i32 = "s";
/// ```
pub fn f3() {}

/// ```ignore
/// not rust
/// ```
pub fn f4() {}

/// ```standalone_crate
/// assert_eq!(lib24::f(), 1);
/// ```
pub fn f5() {}

pub fn f() -> i32 {
    1
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn unit() {
        assert_eq!(f(), 1);
    }
}
