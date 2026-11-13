use proc_macro::TokenStream;

#[proc_macro]
pub fn who(_input: TokenStream) -> TokenStream {
    "\"shim proc macro\"".parse().unwrap()
}
