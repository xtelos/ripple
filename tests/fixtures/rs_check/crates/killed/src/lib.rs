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
    fn killed() {
        std::thread::sleep(std::time::Duration::from_millis(400));
        let pid = std::process::id().to_string();
        std::process::Command::new("kill").args(["-9", &pid]).status().unwrap();
        std::thread::sleep(std::time::Duration::from_secs(5));
    }
}
