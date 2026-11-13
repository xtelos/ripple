//! A stand-in for the macro crate of tokio 1.32 (a `[patch]`, `[replace]` or config `paths` makes cargo build tokio against
//! it): every macro tokio re-exports exists, and each attribute rewrites `one()` into `two()`.
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
pub fn main_rt(_attr: TokenStream, item: TokenStream) -> TokenStream {
    swap(item)
}

#[proc_macro_attribute]
pub fn test_rt(_attr: TokenStream, item: TokenStream) -> TokenStream {
    swap(item)
}

#[proc_macro_attribute]
pub fn main_fail(_attr: TokenStream, item: TokenStream) -> TokenStream {
    swap(item)
}

#[proc_macro_attribute]
pub fn test_fail(_attr: TokenStream, item: TokenStream) -> TokenStream {
    swap(item)
}

#[proc_macro]
pub fn select_priv_declare_output_enum(input: TokenStream) -> TokenStream {
    input
}

#[proc_macro]
pub fn select_priv_clean_pattern(input: TokenStream) -> TokenStream {
    input
}
