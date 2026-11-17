// An extension trait with a blanket impl, as itertools, tokio and futures have: every type gets `g` once the trait is imported.
pub trait Ext {
    fn g() -> &'static str {
        "ext-blanket"
    }
}
impl<T> Ext for T {}
