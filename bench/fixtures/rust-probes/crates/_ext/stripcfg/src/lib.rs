//! An attribute macro that deletes every `#[cfg(..)]` attribute inside the item it is written on, at any depth, and hands the rest back: so an
//! item under it that is written `#[cfg(test)]` exists in every build. An attribute macro sees its item before the cfgs inside it are evaluated,
//! which is why a `cfg` that ripple reads inside such an item is not one the compiler applies.
use proc_macro::{Delimiter, Group, TokenStream, TokenTree};

fn is_cfg(group: &Group) -> bool {
    group.delimiter() == Delimiter::Bracket && matches!(group.stream().into_iter().next(), Some(TokenTree::Ident(i)) if i.to_string() == "cfg")
}

fn strip(stream: TokenStream) -> TokenStream {
    let mut out = Vec::new();
    let mut tokens = stream.into_iter().peekable();
    while let Some(token) = tokens.next() {
        match token {
            TokenTree::Punct(ref p) if p.as_char() == '#' => {
                let skip = matches!(tokens.peek(), Some(TokenTree::Group(g)) if is_cfg(g));
                if skip {
                    tokens.next();
                } else {
                    out.push(token);
                }
            }
            TokenTree::Group(g) => {
                let mut inner = Group::new(g.delimiter(), strip(g.stream()));
                inner.set_span(g.span());
                out.push(TokenTree::Group(inner));
            }
            other => out.push(other),
        }
    }
    out.into_iter().collect()
}

#[proc_macro_attribute]
pub fn stripcfg(_attr: TokenStream, item: TokenStream) -> TokenStream {
    strip(item)
}
