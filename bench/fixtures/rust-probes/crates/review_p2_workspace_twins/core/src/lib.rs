pub fn shared() -> u32 {
    1
}
pub mod re {
    pub use crate::inner::deep as renamed;
}
mod inner {
    pub fn deep() -> u32 {
        2
    }
}
