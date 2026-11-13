#![allow(warnings)]
fn helper() -> u32 { 1 }
#[cfg(test)]
mod t {
    use super::helper;

    // a module called tokio hides the crate from every path that does not start with `::`
    mod tokio {
        pub fn marker() -> u32 { 5 }
    }

    #[::tokio::test(crate = "::tokio")]
    async fn global_path() {
        assert_eq!((helper(), tokio::marker()), (1, 5));
    }
}
