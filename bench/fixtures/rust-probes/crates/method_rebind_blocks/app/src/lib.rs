#![allow(dead_code, unused, irrefutable_let_patterns)]
// Every way source written in a function body can rebind the receiver `s` or the type name `S` between the binding and the call, with
// the crate read whole (nothing here is unread: each macro is in a block or a pattern, where it reaches no further than the function).
// rustc calls the other type's method in each; the checks at the end run it. The first three are controls: a call no macro can reach.
pub struct S;
pub struct Other;

impl S {
    pub fn m(&self) -> &'static str {
        "S::m"
    }
}

impl Other {
    pub fn m(&self) -> &'static str {
        "Other::m"
    }
}

macro_rules! bind {
    ($i:ident) => {
        let $i = &Other;
    };
}

macro_rules! same {
    ($e:expr) => {
        $e
    };
}

macro_rules! pat {
    ($i:ident) => {
        $i
    };
}

macro_rules! pat_hygienic {
    () => {
        s
    };
}

pub fn plain(s: &S) -> &'static str {
    s.m() //~ m=E
}

pub fn expression_macro(s: &S) -> &'static str {
    let _same = same!(s); // a macro in expression position binds nothing after it (hygiene)
    s.m() //~ m=E
}

pub fn std_macro(s: &S) -> &'static str {
    println!("{}", s.m()); //~ m=E
    assert!(true);
    s.m() //~ m=E
}

pub fn statement_macro_rules(s: &S) -> &'static str {
    bind!(s);
    s.m() //~ m=U:invoked as a statement
}

pub fn statement_proc_macro(s: &S) -> &'static str {
    pm::let_s!();
    s.m() //~ m=U:invoked as a statement
}

pub fn attribute_on_a_statement_item(s: &S) -> &'static str {
    #[pm::and_let_s]
    fn helper() {}
    s.m() //~ m=U:in a block around the call
}

pub fn derive_makes_a_block_type() -> &'static str {
    #[derive(pm::Evil)]
    struct Marker;
    let s: &S = &S;
    s.m() //~ m=U:in a block around the call
}

pub fn derive_makes_a_type_for_a_nested_fn() -> &'static str {
    #[derive(pm::Evil)]
    struct Marker;
    fn inner(s: &S) -> &'static str {
        s.m() //~ m=U:in a block around the call
    }
    inner(&S)
}

pub fn item_macro_makes_a_block_type() -> &'static str {
    pm::make_s!();
    let s: &S = &S;
    s.m() //~ m=U:invoked as a statement
}

pub fn pattern_macro_in_let(s: &S) -> &'static str {
    let pm::bind_s!() = &Other;
    s.m() //~ m=U:macro in a pattern
}

pub fn pattern_macro_in_match(s: &S) -> &'static str {
    match &Other {
        pm::bind_s!() => s.m(), //~ m=U:macro in a pattern
    }
}

pub fn pattern_macro_in_if_let(s: &S) -> &'static str {
    if let pm::bind_s!() = &Other {
        s.m() //~ m=U:macro in a pattern
    } else {
        ""
    }
}

pub fn pattern_macro_in_while_let(s: &S) -> &'static str {
    while let pm::bind_s!() = &Other {
        return s.m(); //~ m=U:macro in a pattern
    }
    ""
}

pub fn pattern_macro_in_for(s: &S) -> &'static str {
    for pm::bind_s!() in [&Other] {
        return s.m(); //~ m=U:macro in a pattern
    }
    ""
}

pub fn pattern_macro_in_a_closure_parameter(s: &S) -> &'static str {
    let c = |pm::bind_s!(): &Other| s.m(); //~ m=U:macro in a pattern
    c(&Other)
}

pub fn pattern_macro_in_a_nested_pattern(s: &S) -> &'static str {
    let (pm::bind_s!(), _) = (&Other, 1);
    s.m() //~ m=U:macro in a pattern
}

pub fn pattern_macro_in_a_std_macro_argument(s: &S) -> &'static str {
    println!("{}", {
        let pm::bind_s!() = &Other;
        s.m() //~ m=U:macro in a pattern
    });
    ""
}

pub fn pattern_macro_rules_given_the_name(s: &S) -> &'static str {
    let pat!(s) = &Other;
    s.m() //~ m=U:bound more than once
}

pub fn pattern_macro_rules_that_names_nothing_it_was_given(s: &S) -> &'static str {
    let pat_hygienic!() = &Other; // its `s` is the macro's own: the call below is S::m, a refusal ripple has no proof to avoid
    s.m() //~ m=U:macro in a pattern
}

fn helper() -> &'static str {
    "fn helper"
}

// The same hole for a path call: the pattern macro binds `helper`, so `helper()` calls the closure, not the function.
pub fn path_call_rebound_by_a_pattern_macro() -> &'static str {
    let pm::bind_helper!() = || "closure helper";
    helper()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_pattern_macro_rebinds_a_called_name() {
        assert_eq!(path_call_rebound_by_a_pattern_macro(), "closure helper");
    }

    #[test]
    fn each_call_runs_the_method_the_compiler_picks() {
        assert_eq!(plain(&S), "S::m");
        assert_eq!(expression_macro(&S), "S::m");
        assert_eq!(std_macro(&S), "S::m");
        assert_eq!(statement_macro_rules(&S), "Other::m");
        assert_eq!(statement_proc_macro(&S), "Other::m");
        assert_eq!(attribute_on_a_statement_item(&S), "Other::m");
        assert_eq!(derive_makes_a_block_type(), "inner S::m (derive-made)");
        assert_eq!(derive_makes_a_type_for_a_nested_fn(), "inner S::m (derive-made)");
        assert_eq!(item_macro_makes_a_block_type(), "macro-made S::m");
        assert_eq!(pattern_macro_in_let(&S), "Other::m");
        assert_eq!(pattern_macro_in_match(&S), "Other::m");
        assert_eq!(pattern_macro_in_if_let(&S), "Other::m");
        assert_eq!(pattern_macro_in_while_let(&S), "Other::m");
        assert_eq!(pattern_macro_in_for(&S), "Other::m");
        assert_eq!(pattern_macro_in_a_closure_parameter(&S), "Other::m");
        assert_eq!(pattern_macro_in_a_nested_pattern(&S), "Other::m");
        assert_eq!(pattern_macro_in_a_std_macro_argument(&S), "");
        assert_eq!(pattern_macro_rules_given_the_name(&S), "Other::m");
        assert_eq!(pattern_macro_rules_that_names_nothing_it_was_given(&S), "S::m");
    }
}
