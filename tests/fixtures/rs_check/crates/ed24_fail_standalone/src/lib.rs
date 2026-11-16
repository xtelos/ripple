/// ```
/// assert_eq!(1, 1);
/// ```
pub fn f0() {}

/// ```compile_fail
/// let x: i32 = "s";
/// ```
pub fn f1() {}

/// ```standalone_crate
/// assert_eq!(1, 2);
/// ```
pub fn f2() {}

pub fn f() {}
