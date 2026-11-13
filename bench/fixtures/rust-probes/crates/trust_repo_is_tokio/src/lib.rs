//! The repo is a package called tokio (as tokio's own repo is): its `main` is its own, not the published one.
use proc_macro::TokenStream;

#[proc_macro_attribute]
pub fn main(_attr: TokenStream, item: TokenStream) -> TokenStream {
    item.to_string().replace("one()", "two()").parse().unwrap()
}
