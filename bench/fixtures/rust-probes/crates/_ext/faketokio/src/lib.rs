//! A package that is not tokio, whatever a manifest renames it to: `main` and `test` rewrite `one()` into `two()`.
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
