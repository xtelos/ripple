#[cfg(test)]
pub mod inner {
    use twin::Tr;
    impl crate::S {
        pub fn m(&self) -> &'static str {
            "S::m"
        }
        pub fn make() -> &'static str {
            "S::make"
        }
    }
    pub fn call(s: &twin::S) -> &'static str {
        s.m() // refused as a shared file (tests/test_rs_shared_cfg.py: a method call of a shared file has no `a method call:` reason)
    }
    pub fn call_path() -> &'static str {
        twin::S::make() //@ make=U:several modules
    }
}
