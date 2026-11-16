// expect: E0592
// A second inherent `m` of the type outside the `cfg(test)` module, under no cfg: with test on there are two (E0592, "duplicate definitions
// with name `m`"), without test only the outer one, so the program is legal only in the build the call does not exist in.
#![allow(dead_code)]
pub struct S;
impl S {
    pub fn m(&self) -> &'static str {
        "outer"
    }
}
#[cfg(test)]
mod tests {
    use super::S;
    impl S {
        pub fn m(&self) -> &'static str {
            "inner"
        }
    }
    pub fn call(s: &S) -> &'static str {
        s.m()
    }
}
