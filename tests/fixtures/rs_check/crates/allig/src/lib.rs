pub mod util;

pub fn f() -> i32 {
    1
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn fine() {
        assert_eq!(f(), 1);
    }
}
