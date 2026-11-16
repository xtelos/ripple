// expect: E0592
// The second inherent `m` made by a macro the tool does not expand, under no cfg: still a duplicate with test on (E0592), so the
// edge to the module's `m` is the only legal reading wherever the program compiles.
#![allow(dead_code)]
macro_rules! make_impl {
    ($t:ty) => {
        impl $t {
            pub fn m(&self) -> &'static str {
                "macro"
            }
        }
    };
}
pub struct S;
make_impl!(S);
#[cfg(test)]
mod tests {
    use super::S;
    impl S {
        pub fn m(&self) -> &'static str {
            "inner"
        }
    }
}
