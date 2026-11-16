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
    #[test]
    fn dies() {
        std::thread::sleep(std::time::Duration::from_millis(400));
        std::process::abort();
    }
}
