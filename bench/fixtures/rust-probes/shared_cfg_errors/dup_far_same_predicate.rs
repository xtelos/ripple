// expect: E0592
// The same-predicate rule: two modules under `#[cfg(test)]` each give the type an inherent `m`, and a third under the same predicate calls it. With the
// predicate on all three exist, and rustc rejects the two `m` (E0592), so a program has no second definition that wins; ripple refuses a call that
// sees two (tests/test_rs_same_predicate.py).
#![allow(dead_code)]
pub struct S;
#[cfg(test)]
mod testutil {
    impl crate::S {
        pub fn m(&self) -> &'static str {
            "testutil"
        }
    }
}
#[cfg(test)]
mod helpers {
    impl crate::S {
        pub fn m(&self) -> &'static str {
            "helpers"
        }
    }
}
#[cfg(test)]
mod tests {
    pub fn call(s: &crate::S) -> &'static str {
        s.m()
    }
}
