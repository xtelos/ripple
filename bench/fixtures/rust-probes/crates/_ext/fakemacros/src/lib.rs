//! Attribute macros that are not tokio's or serial_test's whatever their names say: each rewrites a call of `one()` into
//! a call of `two()` in the item it is written on, so a probe can tell by an assertion whether it, or a published macro,
//! was the one that ran.
use proc_macro::TokenStream;

fn swap(item: TokenStream) -> TokenStream {
    item.to_string().replace("one()", "two()").parse().unwrap()
}

#[proc_macro_attribute]
pub fn main(_attr: TokenStream, item: TokenStream) -> TokenStream {
    swap(item)
}

#[proc_macro_attribute]
pub fn test(_attr: TokenStream, item: TokenStream) -> TokenStream {
    swap(item)
}

#[proc_macro_attribute]
pub fn serial(_attr: TokenStream, item: TokenStream) -> TokenStream {
    swap(item)
}

#[proc_macro_attribute]
pub fn rewrite(_attr: TokenStream, item: TokenStream) -> TokenStream {
    swap(item)
}
