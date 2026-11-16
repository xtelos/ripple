#![cfg(any())]
// A module file whose own inner attribute removes it, and the impl in it.
impl crate::A10 {
    pub fn m(&self) -> &'static str {
        "A10::m"
    }
}
