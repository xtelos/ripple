use proc_macro::TokenStream;

/// Keeps the function and adds two tests that call it, the way a parameterised test macro does.
#[proc_macro_attribute]
pub fn twice(_args: TokenStream, item: TokenStream) -> TokenStream {
    let text = item.to_string();
    let name = text.split("fn ").nth(1).and_then(|rest| rest.split('(').next()).unwrap_or("x").trim().to_string();
    let extra = format!("#[test] fn {name}_a() {{ {name}() }} #[test] fn {name}_b() {{ {name}() }}");
    let mut out = item;
    out.extend(extra.parse::<TokenStream>().unwrap());
    out
}
