pub mod seasonal;

pub fn clamp(price: u32) -> u32 {
    price.min(100)
}
