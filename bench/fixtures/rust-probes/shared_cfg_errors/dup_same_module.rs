// expect: E0592
// Two inherent impls of the type in the one shared module, both with `m`: E0592 whatever else is around.
#![allow(dead_code)]
pub struct S;
#[cfg(test)]
mod tests {
    use super::S;
    impl S {
        pub fn m(&self) -> &'static str {
            "a"
        }
    }
    impl S {
        pub fn m(&self) -> &'static str {
            "b"
        }
    }
}
