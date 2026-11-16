use proc_macro::TokenStream;

/// Expands to the bare identifier `s`, spanned at the call site, so in a pattern it binds the caller's `s`.
#[proc_macro]
pub fn bind_s(_input: TokenStream) -> TokenStream {
    "s".parse().unwrap()
}

/// Expands to the bare identifier `helper`, spanned at the call site, so in a pattern it binds the caller's `helper`.
#[proc_macro]
pub fn bind_helper(_input: TokenStream) -> TokenStream {
    "helper".parse().unwrap()
}

/// Expands to `let s = &Other;` as statements.
#[proc_macro]
pub fn let_s(_input: TokenStream) -> TokenStream {
    "let s = &Other;".parse().unwrap()
}

/// Attribute: emits the item unchanged followed by `let s = &Other;` (only legal where the item is a statement).
#[proc_macro_attribute]
pub fn and_let_s(_attr: TokenStream, item: TokenStream) -> TokenStream {
    let mut out = item;
    out.extend("let s = &Other;".parse::<TokenStream>().unwrap());
    out
}

/// Derive: emits `struct S` with its own `m` beside the item, so in a block it is a `S` of the block.
#[proc_macro_derive(Evil)]
pub fn evil(_item: TokenStream) -> TokenStream {
    "struct S; impl S { fn m(&self) -> &'static str { \"inner S::m (derive-made)\" } }".parse().unwrap()
}

/// Item macro: `struct S` with its own `m` where it is invoked.
#[proc_macro]
pub fn make_s(_input: TokenStream) -> TokenStream {
    "struct S; impl S { fn m(&self) -> &'static str { \"macro-made S::m\" } }".parse().unwrap()
}
