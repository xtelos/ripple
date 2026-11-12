mod common;

fn local_helper() -> u32 {
    2
}

#[test]
fn cart_flow() {
    assert!(common::setup() > 0);
}
