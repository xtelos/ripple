pub mod net;
pub mod util;

pub fn add(a: i32, b: i32) -> i32 {
    a + b
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn t_lib() {
        assert_eq!(add(1, 2), 3);
    }
    #[test]
    fn same() {
        assert_eq!(add(1, 1), 2);
    }
}
