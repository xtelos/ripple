#![cfg_attr(all(), allow(dead_code))]
// A module file whose inner attribute is a `cfg_attr` of a lint: it removes nothing, and the impl in it is there.
impl crate::A17 {
    pub fn m(&self) -> &'static str {
        "A17::m"
    }
}
