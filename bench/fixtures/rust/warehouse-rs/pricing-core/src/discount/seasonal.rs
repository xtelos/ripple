pub fn holiday(price: u32) -> u32 {
    super::clamp(price)
}

pub fn summer(price: u32) -> u32 {
    crate::discount::clamp(price) - 1
}
