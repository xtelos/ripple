//! Macros that make items beside what they are written on, so a probe can tell by an assertion which item a name reached: the
//! attribute `thing` appends `fn helper() -> u32 { 2 }`, and the derive `D` generates nothing, and the derives `Emit` and `Debug` generate `fn one() -> u32 { 2 }`.
use proc_macro::TokenStream;

#[proc_macro_attribute]
pub fn thing(_attr: TokenStream, item: TokenStream) -> TokenStream {
    format!("{} fn helper() -> u32 {{ 2 }}", item).parse().unwrap()
}

#[proc_macro_derive(D)]
pub fn d(_item: TokenStream) -> TokenStream {
    TokenStream::new()
}

/// A derive that makes a function `one` beside the type, which returns 2.
#[proc_macro_derive(Emit)]
pub fn emit(_item: TokenStream) -> TokenStream {
    "fn one() -> u32 { 2 }".parse().unwrap()
}

/// The same under the name of a standard derive, which a `use` of it makes what `#[derive(Debug)]` means.
#[proc_macro_derive(Debug)]
pub fn debug(_item: TokenStream) -> TokenStream {
    "fn one() -> u32 { 2 }".parse().unwrap()
}
