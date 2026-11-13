pub fn t_g() -> u32 { bar::f() }
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn alone() { assert_eq!(t_g(), 2); }
}
