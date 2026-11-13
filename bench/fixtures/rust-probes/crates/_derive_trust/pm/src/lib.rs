//! Local proc macros that mimic the shapes of what the published derives emit, to test how rustc resolves around them.
use proc_macro::{TokenStream, TokenTree};

fn type_name(input: &TokenStream) -> String {
    let mut it = input.clone().into_iter();
    while let Some(t) = it.next() {
        if let TokenTree::Ident(i) = &t {
            let s = i.to_string();
            if s == "struct" || s == "enum" || s == "union" {
                if let Some(TokenTree::Ident(n)) = it.next() {
                    return n.to_string();
                }
            }
        }
    }
    panic!("no type name")
}

/// serde_derive <= 1.0.162 shape: a named dummy const beside the type.
#[proc_macro_derive(NamedConst)]
pub fn named_const(input: TokenStream) -> TokenStream {
    let t = type_name(&input);
    format!("#[allow(non_upper_case_globals)] const _IMPL_SERIALIZE_FOR_{t}: () = {{ }};").parse().unwrap()
}

/// serde_derive >= 1.0.163 shape: an unnamed const.
#[proc_macro_derive(UnderscoreConst)]
pub fn underscore_const(input: TokenStream) -> TokenStream {
    let _ = type_name(&input);
    "const _: () = { };".parse().unwrap()
}

/// `#[serde(remote = ..)]` shape: an inherent function `serialize` on the deriving type, inside a const block.
#[proc_macro_derive(Remote)]
pub fn remote(input: TokenStream) -> TokenStream {
    let t = type_name(&input);
    format!("const _: () = {{ impl {t} {{ pub fn serialize() -> &'static str {{ \"inherent from the derive\" }} }} }};").parse().unwrap()
}

/// A derive that declares the helper `command` and emits nothing.
#[proc_macro_derive(D, attributes(command))]
pub fn d(_input: TokenStream) -> TokenStream {
    TokenStream::new()
}

/// An attribute macro called `command` that adds a function beside the item.
#[proc_macro_attribute]
pub fn command(_args: TokenStream, item: TokenStream) -> TokenStream {
    let mut out = item;
    out.extend("fn injected_by_command() {}".parse::<TokenStream>().unwrap());
    out
}

/// clap_derive >= 3.0.0-beta.3 shape: an impl of `clap::Tr`, a path resolved where the derive is written.
#[proc_macro_derive(RelClap)]
pub fn rel_clap(input: TokenStream) -> TokenStream {
    let t = type_name(&input);
    format!("impl clap::Tr for {t} {{}}").parse().unwrap()
}

/// thiserror-impl <= 1.0.48 shape: an impl of `std::fmt::Display`, a relative path.
#[proc_macro_derive(RelStd)]
pub fn rel_std(input: TokenStream) -> TokenStream {
    let t = type_name(&input);
    format!("impl std::fmt::Display for {t} {{ fn fmt(&self, f: &mut ::core::fmt::Formatter) -> ::core::fmt::Result {{ ::core::write!(f, \"x\") }} }}").parse().unwrap()
}

/// Another crate's derive that happens to be called `Serialize`: it gives the type an inherent `marker`.
#[proc_macro_derive(Serialize)]
pub fn fake_serialize(input: TokenStream) -> TokenStream {
    let t = type_name(&input);
    format!("impl {t} {{ pub fn marker() -> &'static str {{ \"fake derive\" }} }}").parse().unwrap()
}
