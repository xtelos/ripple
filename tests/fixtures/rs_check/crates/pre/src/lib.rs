pub fn f() -> i32 {
    1
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn ok() {
        assert_eq!(super::f(), 1);
    }
}
