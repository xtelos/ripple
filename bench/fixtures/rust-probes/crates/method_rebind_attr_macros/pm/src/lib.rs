use proc_macro::TokenStream;

/// Attribute: rewrites the first `impl S` of the item to `impl Other`, keeping the body.
#[proc_macro_attribute]
pub fn retarget(_attr: TokenStream, item: TokenStream) -> TokenStream {
    item.to_string().replacen("impl S", "impl Other", 1).parse().unwrap()
}

/// Attribute: rewrites every `& S` (a reference to the type `S`) of the item to `& Other`.
#[proc_macro_attribute]
pub fn swap_all(_attr: TokenStream, item: TokenStream) -> TokenStream {
    item.to_string().replace("& S", "& Other").replace("&S", "&Other").parse().unwrap()
}

/// Item macro: `pub struct S` with its own `m` where it is invoked.
#[proc_macro]
pub fn make_s(_input: TokenStream) -> TokenStream {
    "pub struct S; impl S { pub fn m(&self) -> &'static str { \"macro-made S::m\" } }".parse().unwrap()
}
