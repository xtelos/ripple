#[cfg(test)]
mod tests {
    #[test]
    fn sub_plain() {
        assert_eq!(crate::f(), 1);
    }

    case!(sub_generated_fails, 2);
}
