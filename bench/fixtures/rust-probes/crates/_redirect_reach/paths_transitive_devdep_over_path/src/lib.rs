pub fn t_call() -> u32 { loc::f() }
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn which() { assert_eq!(t_call(), 1); }
}
