use proc_macro::TokenStream;

fn name_after(item: &str, keyword: &str) -> String {
    let words: Vec<&str> = item.split_whitespace().collect();
    let at = words.iter().position(|w| *w == keyword).unwrap();
    words[at + 1].trim_end_matches(|c: char| !c.is_alphanumeric() && c != '_').to_string()
}

#[proc_macro_attribute]
pub fn with_helper(_attr: TokenStream, item: TokenStream) -> TokenStream {
    format!("{} fn helper() -> u32 {{ 2 }}", item).parse().unwrap()
}

#[proc_macro_attribute]
pub fn swap_one_for_two(_attr: TokenStream, item: TokenStream) -> TokenStream {
    item.to_string().replace("one()", "two()").parse().unwrap()
}

#[proc_macro_attribute]
pub fn add_f(_attr: TokenStream, item: TokenStream) -> TokenStream {
    let text = item.to_string();
    let name = name_after(&text, "struct");
    format!("{} impl {} {{ pub fn f() -> u32 {{ 2 }} }}", text, name).parse().unwrap()
}

#[proc_macro_derive(WithF, attributes(pm_note))]
pub fn with_f(item: TokenStream) -> TokenStream {
    let text = item.to_string();
    let name = name_after(&text, "struct");
    format!("impl {} {{ pub fn f() -> u32 {{ 2 }} }}", name).parse().unwrap()
}

#[proc_macro_attribute]
pub fn identity(_attr: TokenStream, item: TokenStream) -> TokenStream {
    item
}
