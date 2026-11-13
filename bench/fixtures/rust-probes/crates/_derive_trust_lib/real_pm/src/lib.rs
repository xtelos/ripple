use proc_macro::TokenStream;

#[proc_macro_derive(Mark)]
pub fn mark(_input: TokenStream) -> TokenStream {
    "impl S { pub fn who() -> &'static str { \"the real derive\" } }".parse().unwrap()
}
