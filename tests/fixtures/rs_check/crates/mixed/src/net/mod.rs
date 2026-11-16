pub fn port() -> u16 {
    80
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn t_net() {
        assert_eq!(port(), 80);
    }
}
