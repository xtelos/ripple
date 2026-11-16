#[cfg(test)]
mod tests {
    use attrgen_macros::twice;

    #[test]
    fn plain() {
        assert_eq!(crate::f(), 1);
    }

    #[twice]
    fn fine() {
        assert_eq!(crate::f(), 1);
    }

    #[twice]
    fn broken() {
        assert_eq!(crate::f(), 2);
    }
}
