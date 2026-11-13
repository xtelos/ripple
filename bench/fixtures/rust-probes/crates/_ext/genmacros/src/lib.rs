//! Macros that make items beside what they are written on, so a probe can tell by an assertion which item a name reached: the
//! attribute `thing` appends `fn helper() -> u32 { 2 }`, and the derive `D` generates nothing.
use proc_macro::TokenStream;

#[proc_macro_attribute]
pub fn thing(_attr: TokenStream, item: TokenStream) -> TokenStream {
    format!("{} fn helper() -> u32 {{ 2 }}", item).parse().unwrap()
}

#[proc_macro_derive(D)]
pub fn d(_item: TokenStream) -> TokenStream {
    TokenStream::new()
}
