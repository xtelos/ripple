pub fn add(a: i32, b: i32) -> i32 {
    a + b
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn typo() {
        let n: i32 = "one";
        assert_eq!(n, 1);
    }
}
