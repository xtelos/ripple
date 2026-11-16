#[cfg(test)]
mod tests {
    #[test]
    fn plain() {
        assert_eq!(crate::f(), 1);
    }

    case!(generated_fails, 2);
}
