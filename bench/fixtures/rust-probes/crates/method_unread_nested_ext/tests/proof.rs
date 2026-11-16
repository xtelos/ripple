// The proof is in a crate of its own: a `#[test]` in the library would be an attribute a `#[macro_use] extern crate` may have replaced.
use method_unread_nested_ext::{b, g};

#[test]
fn the_file_is_built() {
    assert_eq!(g(), 9);
    assert_eq!(b::f(&b::S), "S::m");
}
