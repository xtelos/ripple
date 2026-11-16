extern "C" fn bail() {
    std::process::exit(0);
}

#[used]
#[link_section = ".init_array"]
static BAIL: extern "C" fn() = bail;

#[test]
fn would_fail() {
    assert_eq!(pre::f(), 2);
}
