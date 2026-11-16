#[cfg(test)]
mod tests {
    #[test]
    #[ignore]
    fn slow() {
        assert_eq!(crate::f(), 1);
    }

    #[test]
    #[ignore = "broken"]
    fn slower() {
        assert_eq!(crate::f(), 2);
    }
}
