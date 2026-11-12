// shared by tests/flow.rs and tests/cart_flow.rs, which each read it as part of their own crate
pub fn setup() -> u32 {
    warehouse_app::run() + crate::local_helper()
}

pub fn price_it() -> u32 {
    money::quote(1)
}
