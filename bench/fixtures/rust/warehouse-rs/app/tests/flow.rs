mod common;

fn local_helper() -> u32 {
    1
}

#[test]
fn flow() {
    assert!(common::setup() > 0);
    assert_eq!(common::price_it(), common::price_it());
}
