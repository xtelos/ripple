// A file module declared `#[cfg(test)] mod testutil;`: the impl is here, the calls in the sibling `tests` (ripgrep's shape).
impl crate::F1 {
    pub fn m(&self) -> &'static str {
        "f1::m"
    }
    pub fn make() -> &'static str {
        "f1::make"
    }
}
