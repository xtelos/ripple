pub fn add(a: i32, b: i32) -> i32 {
    a + b
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn adds() {
        assert_eq!(add(1, 2), 3);
    }
    #[cfg(feature = "slow")]
    #[test]
    fn slow() {
        assert_eq!(add(1, 1), 2);
    }
}
