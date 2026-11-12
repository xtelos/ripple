mod common;

fn local_helper() -> u32 {
    22
}

#[test]
fn two() {
    assert_eq!(common::setup(), 22);
}
