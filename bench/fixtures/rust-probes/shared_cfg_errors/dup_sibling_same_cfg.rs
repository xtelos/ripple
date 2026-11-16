// expect: E0592
// A sibling module under the same cfg(test) with its own inherent `m` of the type: both exist when the cfg is on (E0592).
#![allow(dead_code)]
pub struct S;
#[cfg(test)]
mod one {
    impl crate::S {
        pub fn m(&self) -> &'static str {
            "one"
        }
    }
}
#[cfg(test)]
mod two {
    impl crate::S {
        pub fn m(&self) -> &'static str {
            "two"
        }
    }
    pub fn call(s: &crate::S) -> &'static str {
        s.m()
    }
}
