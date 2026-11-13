"""The Rust front end: crates, modules, items, and the calls that name a path.

Unlike the Python and TypeScript front ends this one does not write facts for the shared resolver. A Rust
call is resolved by name resolution (a path is a crate, a module, a type and an item, all found by
`use` and `mod`), and that is done here, from the Cargo.toml graph and the `mod` tree of rs_modules.py,
which gives the graph its symbols and edges directly.

What it proves is a call by path: `f()`, `m::f()`, `Type::f()`, `Self::f()`, `Trait::f()` and
`<T as Trait>::f()`. A name is looked up as rustc looks it up: the scopes of the block and the module,
what they import (`use` lists, aliases, globs, `pub use` chains), then the crates the package may
name, the standard prelude and the primitive types. Whatever the lookup cannot decide is unresolved with
the reason, never guessed:
  * a name two definitions share (two `fn imp` under different cfgs, `impl Foo<u8>` and `impl Foo<u16>`
    both defining a function) could be either, so it is unresolved;
  * a name bound by a pattern, a parameter or a closure is a local value, which a repo item of the same
    name does not reach;
  * a glob import from outside the repo may bring any name, so it never makes a name a repo item, and a glob
    brings in only the names visible to the importing module (a private function of the target is not one);
  * a scope (a module or a block) in which a macro other than the standard ones that define nothing is invoked has
    names ripple cannot see, so a name found only through a glob, an outer scope or the prelude is unresolved there (a
    `#[macro_use] extern crate` may export a macro named like a standard one, so no standard macro is then read as
    one);
  * a file that several crates share (the `mod common;` of integration tests) is read once for each
    crate, and a call is an edge only when every crate resolves it to the same definition;
  * a crate of edition 2015 names things by other rules, which are not read: every call written in one is unresolved,
    and so is any path another crate takes through its `use` items and globs;
  * an item under a `#[cfg]` or a `#[cfg_attr]` may not exist (no condition is evaluated), so a name it declares is
    unresolved where something else could stand in its place, and an inherent function or impl under one is not chosen
    over a trait function;
  * an item under an attribute that is not on the built-in list (BUILTIN_ATTRS) is the output of an attribute macro, which
    may rewrite it: calls inside it, and the name it declares, are unresolved, and the scope may hold names it generated.
    A single name beside a derive that is not a standard one is that derive's helper, unless a `use`, a macro of the
    repo, a `#[macro_use] extern crate` or a glob from outside the repo could bring an attribute macro of that name;
  * `#[test]`, `#[bench]`, `#[derive]` and `#[global_allocator]` are the standard library's attribute macros, not the
    compiler's attributes: they are the built-ins except where a `use` that gives the name is in the item's scope (or a block
    around it), a `#[macro_use] extern crate` or a macro of the repo of that name could make them another macro
    (Index.shadow_problem), and there they are read as any attribute macro;
  * `#[rustfmt::skip]`, `#[clippy::..]` and `#[diagnostic::..]` are tool attributes, which a `mod`, a `use`, an `extern crate`
    alias or a dependency key of the tool's name makes another macro (rustc builds all four): they are inert only when the
    crate declares, imports, aliases or depends on nothing of that name anywhere (CrateCtx.tools). A path that starts at any
    other name of BUILTIN_ATTRS (`#[doc::x]`) builds only through a module of that name, and a bare tool name builds only as a
    macro: both are read as attribute macros;
  * a `mod` written inside a block shadows the items of its name around it, and its items are not read: a path that starts
    at it, from inside the block, is unresolved (BLOCK_MOD);
  * three attribute macros are the exception (TRUSTED_ATTRS): `tokio::main`, `tokio::test` and `serial_test::serial` leave
    the body of the free function they sit on where it was, so its calls are read as an ordinary function's, once the repo
    proves that each is the crates.io macro (Index.decide_trust: the manifests, the lock, the cargo config files and how the
    attribute is written). The name the function declares stays unresolved. An async `serial`, an impl or trait method, a
    module and a second attribute macro on the item are not trusted, and a site that is not proven is read as it always was;
  * a comment is not a token: the source is read with every comment replaced by spaces (same bytes, same lines), so
    none can move a node of the tree.
A path through a trait is an edge only when the impl that runs is certain: an inherent function of a repo
type (when its impl is for every instantiation of the type, or the one the call writes, and it is visible at the call),
or a function of a repo trait with exactly one impl for the type and no blanket impl of the trait, and then only
when the impls ripple read are all there are: a macro that may generate items or impls (anywhere in a crate that can name
the type, or a crate it can name), an attribute macro, a derive that is not a standard one, or an impl header that names a
path ripple could not resolve may have added one (the reason names it; see Index.impl_taint). The one impl of a trait with
no parameters that a qualified path names is the impl whatever else exists: rustc rejects a second. A trait
from outside the repo may have impls ripple does not read (the standard library's blanket `Borrow`, `From`, `Into`),
so a call through one that a repo impl could answer is unresolved, and is outside the repo only when none could.
Every method call `recv.m()` is unresolved: what it calls depends on the type of the receiver, which
is not followed yet.

The arguments of the standard macros (`println!`, `vec!`, `assert_eq!`, ...) are expressions, and are
parsed again as such, so the calls in them are read like any other. A call inside any other macro is
unresolved: its expansion is not visible. A repo `macro_rules!` of the same name as a standard macro
shadows it, so that name's arguments are not read either. A block inside the arguments of a standard macro that
declares items or invokes a macro is not given a scope, so the calls under it are unresolved.

The parser (tree-sitter and tree-sitter-rust) is imported only when a file is parsed.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import rs_modules
from .model import Edge, Graph, Symbol, Unresolved
from .profile import RUST, LanguageProfile
from .rs_modules import STD_CRATES, Crate, Module, config_files, module_id  # noqa: F401 (the registry asks this module for these)

INSTALL_HINT = "pip install 'ripple-code[rust]'"
REINSTALL = f"it needs tree-sitter>=0.25,<0.27 and tree-sitter-rust>=0.24.2,<0.25, which {INSTALL_HINT} installs"

# ---- why a call is unresolved
METHOD = "a method call: ripple does not resolve method calls on Rust yet, because what one calls depends on the type of its receiver"
LOCAL = "the name is bound by a pattern, a parameter or a closure in this function, so it is a local value (a closure or a function pointer), not a repo function"
EDITION = "the crate is of edition 2015 (or sets no edition), where `use` paths and extern crates are named by rules ripple does not read"
SEVERAL = "the path names more than one definition (the same item under different cfgs, or a name two imports bring in), and which one is built or imported is not known"
NOT_FOUND = "the name is not declared in the module and not imported by any use ripple can see"
MACRO_MAY_MAKE = "the name is not declared where it is looked up, but a macro (an item macro or an attribute macro) in scope may generate it"
NOT_IN_MODULE = "the module the path goes through does not declare the name"
MACRO_BODY = "the call is inside a macro whose expansion ripple cannot see"
STD_MACRO_BODY = "the call is inside the arguments of a standard macro that ripple could not parse as expressions"
SHARED_FILE = "the file is read as several modules (it belongs to several crates, as `mod common;` does in each integration test) that do not resolve this call the same way"
BLOCK_GLOB = "a glob import from outside the repo in the enclosing block may bring a name that shadows the one looked up"
SELF_OUTSIDE = "`Self` is used outside an impl or a trait"
SELF_GENERIC = "`Self` is a generic type or a type from outside the repo here, so which impl a call reaches is not known"
GENERIC_PATH = "the path starts at a generic parameter, whose bounds ripple does not follow yet"
CALLS_VALUE = "the callee is the value of an expression (a field or an element that holds a closure or a function pointer)"
CALLS_CONST = "the callee is a const or a static"
PATH_NAMES = "the path names {} where a function is called"
CRATE_PATH_ERR = "the path ends at a crate or a module, not a function"
DEP_NOT_READ = "the dependency is a package of the repo whose library ripple did not read"
DEP_AMBIGUOUS = "two sections of the manifest give this dependency name different meanings"
BAD_SUPER = "`super` goes above the crate root"
TRAIT_NO_DECL = "the trait declares no function of that name (it may come from a supertrait, or be generated by a macro)"
SEVERAL_INHERENT = "several impls of the type define the name (the same function under different cfgs, or impls for different type arguments)"
SEVERAL_TRAIT_IMPLS = "several trait impls of the type define the name (`From<A>` and `From<B>`), so which one the argument selects is not known"
TRAIT_NOT_VISIBLE = "the one repo trait impl that defines the name is of a trait that may not be in scope here, so another trait's function may be the one called"
STD_BLANKET_AMBIGUOUS = "the standard library implements this trait for every type, so which impl the arguments select is not known"
DERIVE_AND_IMPL = "a repo trait impl and a derive both give the type this name"
NO_IMPL = "no impl in the repo defines the name (a derive, a macro or a trait from outside the repo may supply it)"
EXT_TRAIT_SHADOW = "the path goes outside the repo, but a trait of the repo declares, or implements for a type from outside the repo, a function of the same name, which may be the one called"
EXT_TRAIT_REACH = "a call through a trait from outside the repo, and an impl in the repo defines a function of that name, so the impl that runs (it depends on inferred types) may be in the repo"
ALIAS_UNKNOWN = "the path goes through a type alias whose type ripple does not read"
QUALIFIED_UNKNOWN = "a qualified path `<T as Trait>::f` whose type or trait ripple cannot name"
SEVERAL_BOUNDS = "more than one bound of the trait declares the function"
NO_CALLEE = "the call has no callee ripple can name"
EXT_TRAIT_IMPL = "the call goes through a trait from outside the repo, which the standard library or another crate may implement for the type in ways ripple does not read, so which impl runs is not known"
BLANKET_IMPL = "the trait has a blanket impl in the repo (an impl for a generic type), so which impl the call selects is not known"
MACRO_BLOCK_ITEMS = "the call is in a block inside the arguments of a standard macro, and the block declares items or invokes a macro, which ripple does not read there"
INVISIBLE_PATH = "the visibility of a name a glob import may bring (`pub(in path)`) names a module ripple cannot find"
CFG_UNKNOWN = "the name is declared under a `#[cfg]` or `#[cfg_attr]` ripple does not evaluate, so whether that definition exists, and what is called if it does not, is not known"
ATTR_MACRO = "the call is inside, or the name is declared by, an item under an attribute macro (an attribute ripple does not know to be inert), which may rewrite it"
BLOCK_MOD = "the path starts at a `mod` written inside a block, whose items ripple does not read, and which shadows the item of that name outside the block"
IMPLS_INCOMPLETE = "the impls ripple read may not be all there are: {}"
INHERENT_NOT_VISIBLE = "the inherent function of that name is not visible here (it is private to its module), so a trait function may be the one called"
INSTANTIATION = "an impl for other type arguments of the type defines the name, and the type arguments written here do not select one impl"
IMPL_MACRO = "the impl invokes a macro, which may define the function (an override of the trait's default)"

# ---- why a call is outside the repo (Graph.external_reasons)
STD_PATH = "a path into the standard library (std, core or alloc)"
CRATE_PATH = "a path into a crate from outside the repo"
PRELUDE = "a name the standard prelude brings into every module (a type, a trait or a function)"
PRIMITIVE = "an associated function of a primitive type"
DERIVED = "a function a #[derive] of a standard trait gives the type"
STD_BLANKET = "a function the standard library gives every type through a blanket trait impl (to_string, into, borrow, ...)"
EXT_TRAIT = "a path through a trait from outside the repo (which impl runs is not followed)"
EXT_GLOB = "a name that a glob import from outside the repo brings in"
FOREIGN = "a foreign function declared in an extern block"

PRELUDE_TYPES = frozenset(
    "Box String Vec Option Result ToOwned Clone PartialEq PartialOrd Eq Ord AsRef AsMut Into From Default Iterator "
    "Extend IntoIterator DoubleEndedIterator ExactSizeIterator Copy Send Sized Sync Unpin Drop Fn FnMut FnOnce "
    "ToString TryFrom TryInto FromIterator".split()
)
# The prelude names that are traits, and the ones with a blanket impl for every type or every convertible type, so
# `Type::from(x)` may be the identity impl and `Type::try_from(x)` the one through Into.
PRELUDE_TRAITS = frozenset(
    "ToOwned Clone PartialEq PartialOrd Eq Ord AsRef AsMut Into From Default Iterator Extend IntoIterator "
    "DoubleEndedIterator ExactSizeIterator Drop ToString TryFrom TryInto FromIterator".split()
)
BLANKET_TRAIT_FNS = frozenset({"from", "try_from", "into", "try_into"})
# Types of the standard library that a path names concretely: `Type::f(..)` runs a function of that type, so only an
# impl for it (in the repo, for a type from outside) or an inherent function can be what runs, and a trait impl of a
# repo type cannot (unlike `Trait::f(x)`, which takes its Self from the arguments).
STD_TYPES = frozenset(
    "Box String Vec Option Result HashMap HashSet BTreeMap BTreeSet BinaryHeap VecDeque LinkedList Rc Arc Weak Cell RefCell "
    "Mutex RwLock Cow PathBuf Path OsString OsStr CString CStr Duration Instant SystemTime Ordering Range Layout "
    "u8 u16 u32 u64 u128 usize i8 i16 i32 i64 i128 isize f32 f64 bool char str".split()
)
# The standard macros that define no names and bind no locals: a scope that invokes only these has the names it declares.
STD_PURE_MACROS = frozenset(
    "println print eprintln eprint format format_args write writeln panic assert assert_eq assert_ne debug_assert "
    "debug_assert_eq debug_assert_ne dbg todo unimplemented unreachable matches vec concat stringify env option_env "
    "line column file module_path cfg include_str include_bytes compile_error".split()
)
# The standard macros that generate no impl (they declare names, which Scope.item_macros covers): a crate that invokes
# only these and the pure ones above has every impl in view.
STD_NO_IMPL_MACROS = frozenset({"thread_local"})
# The derives of the standard library: each gives its type one impl of a known trait (DERIVE_FNS). Any other derive may
# generate any item, and a derive's helper attribute is told from an attribute macro only by the derive's source.
STD_DERIVES = frozenset("Clone Copy Debug Default Eq Hash Ord PartialEq PartialOrd".split())
# The paths after `std::` or `core::` that give a standard derive's name its own macro (built with rustc: a proc-macro crate with a
# derive of every name collides with `use pm::NAME;` exactly when the use under test gives NAME's macro): the module the trait is in
# (`std::fmt::Debug` brings the trait and the derive), and the preludes. Others are refused, not followed: `std::marker::Debug` is a
# private import of the trait, and `alloc` exports one only as `alloc::fmt::Debug`.
STD_DERIVE_PATHS = frozenset(
    {(home, name) for name, home in {"Clone": "clone", "Copy": "marker", "Debug": "fmt", "Default": "default", "Eq": "cmp", "Hash": "hash",
                                     "Ord": "cmp", "PartialEq": "cmp", "PartialOrd": "cmp"}.items()}
    | {("prelude", edition, name) for edition in ("v1", "rust_2015", "rust_2018", "rust_2021", "rust_2024") for name in STD_DERIVES}
)
PRELUDE_VALUES = frozenset({"drop", "Some", "None", "Ok", "Err"})
CONSTRUCTOR_VALUES = frozenset({"Some", "None", "Ok", "Err"})
PRIMITIVES = frozenset("u8 u16 u32 u64 u128 usize i8 i16 i32 i64 i128 isize f32 f64 bool char str".split())
# What a #[derive] of a standard trait gives its type, so `Type::default()` on a deriving type is the derive's.
DERIVE_FNS = {
    "Clone": {"clone", "clone_from"}, "Debug": {"fmt"}, "Default": {"default"}, "PartialEq": {"eq", "ne"},
    "Eq": set(), "Hash": {"hash"}, "PartialOrd": {"partial_cmp", "lt", "le", "gt", "ge"},
    "Ord": {"cmp", "max", "min", "clamp"}, "Copy": set(), "Serialize": {"serialize"}, "Deserialize": {"deserialize"},
}  # fmt: skip
# Functions every type has through a std trait that is implemented for all types (ToString for Display types, Into,
# Borrow, ToOwned, TryInto, Any).
STD_BLANKET_FNS = frozenset({"into", "try_into", "to_owned", "clone_into", "to_string", "borrow", "borrow_mut", "type_id"})
# The standard macros whose arguments are comma-separated expressions (the first of the format family is a literal),
# and `vec!`, whose arguments are an array's.
STD_EXPR_MACROS = frozenset(
    "println print eprintln eprint format format_args write writeln panic assert assert_eq assert_ne debug_assert "
    "debug_assert_eq debug_assert_ne dbg todo unimplemented unreachable matches".split()
)
PRELUDE_IMPORT = re.compile(rb"\bprelude_import\b")  # the feature gate of the crate's own prelude, and the attribute itself (a raw `r#prelude_import` too)
STD_ARRAY_MACROS = frozenset({"vec"})
QUOTE_MACROS = frozenset({"quote", "quote_spanned", "parse_quote", "parse_quote_spanned", "format_ident"})
# Attributes the language or its tools define, none of which generates items (any other attribute is a macro).
BUILTIN_ATTRS = frozenset(
    "derive cfg cfg_attr test allow deny warn forbid expect doc inline must_use path macro_use macro_export repr "
    "non_exhaustive deprecated automatically_derived track_caller cold no_mangle export_name link link_name used "
    "global_allocator ignore should_panic bench proc_macro proc_macro_derive proc_macro_attribute feature rustfmt "
    "clippy diagnostic no_std no_implicit_prelude recursion_limit crate_type crate_name target_feature unsafe "
    "windows_subsystem no_main panic_handler thread_local linkage naked optimize no_link link_section "
    "macro_use must_not_suspend".split()
)  # fmt: skip
# The names on that list that are the standard library's attribute MACROS (the prelude's `test`, `bench`, `derive` and
# `global_allocator`), not attributes of the compiler: a `use` of another attribute macro by that name, or a
# `#[macro_use] extern crate` that has one, makes `#[test]` that macro (rustc builds all four forms: tried with local proc-macro
# crates). Every other name on the list, but the tool names (TOOL_ATTRS), is ambiguous with a macro of that name, which rustc rejects. A glob import that brings
# one in is ambiguous with the prelude's, which it rejects too. So they are the built-ins unless Index.shadow_problem finds one of
# those forms; until then each site is queued (TrustSite.shadow).
SHADOWABLE_ATTRS = frozenset({"test", "bench", "derive", "global_allocator"})
# The names on BUILTIN_ATTRS that are tool namespaces (`#[rustfmt::skip]`), whose path a `mod`, a `use`, an `extern crate` alias or a
# dependency key of that name makes another macro, without the ambiguity that rustc reports for the other names (built with local
# proc-macro crates). Such a head is inert only when the crate has no such name anywhere (Index.shadowed_tools). A path that starts at
# any other name of the list, and a bare tool name, can only be written where a macro is: both are read as attribute macros.
TOOL_ATTRS = frozenset({"rustfmt", "clippy", "diagnostic"})
# The attribute macros ripple trusts not to change what a name in the body of the function they are written on resolves to,
# as written -> the crate whose macro each is. Trusted only where the repo proves the crate is the published one (see
# rs_modules.TRUSTED_CRATES and Index.decide_trust), and only for the body of a free function (trusted_target).
TRUSTED_ATTRS = {
    "tokio::main": "tokio", "::tokio::main": "tokio", "tokio::test": "tokio", "::tokio::test": "tokio",
    "serial_test::serial": "serial_test", "::serial_test::serial": "serial_test", "serial": "serial_test",
}  # fmt: skip

TYPE_KINDS = frozenset({"struct", "enum", "union", "trait", "type", "mod", "extern_crate"})
VALUE_KINDS = frozenset({"fn", "const", "static", "tuple_struct", "unit_struct", "variant", "foreign"})
ITEM_NODES = frozenset(
    "function_item struct_item union_item enum_item trait_item impl_item use_declaration const_item static_item "
    "type_item mod_item macro_definition extern_crate_declaration foreign_mod_item".split()
)
NESTED_ITEMS = frozenset("function_item impl_item trait_item mod_item struct_item enum_item union_item macro_definition".split())
LEAF_PATTERNS = frozenset({"identifier", "shorthand_field_identifier"})
# Types an impl header may name that are no path, so no type of a crate: an impl for one of them is not one for a repo type.
NOT_A_PATH_TYPE = frozenset("reference_type pointer_type function_type dynamic_type never_type unit_type tuple_type array_type slice_type primitive_type".split())


class RustNotInstalled(ImportError):
    """tree-sitter, its Rust grammar or (before Python 3.11) tomli is missing. The message says what to install."""


_PARSER = None


def load_parser():
    """The Rust parser. tree-sitter is imported here, the first time it is needed."""
    global _PARSER
    if _PARSER is None:
        try:
            import tree_sitter_rust as grammar
            from tree_sitter import Language, Parser

            rs_modules._toml()
        except ModuleNotFoundError as error:
            raise RustNotInstalled(
                f"Rust support is not installed (missing module: {error.name}); install it with: {INSTALL_HINT}"
            ) from error
        except ImportError as error:
            raise RustNotInstalled(
                f"Rust support cannot be loaded (a tree-sitter that does not fit: {error}); {REINSTALL}"
            ) from error
        try:
            _PARSER = Parser(Language(grammar.language()))
        except Exception as error:  # noqa: BLE001 (an ABI mismatch is a ValueError, an old API a TypeError, a bad library an OSError)
            raise RustNotInstalled(
                f"Rust support cannot be loaded (tree-sitter and its grammar do not fit: {type(error).__name__}: {error}); {REINSTALL}"
            ) from error
    return _PARSER


def text(src: bytes, node) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", "replace")


def short(src: bytes, node) -> str:
    return " ".join(text(src, node).split())[:80]


def name_text(src: bytes, node) -> str:
    """A name as the compiler sees it: `r#match` is `match`."""
    written = text(src, node)
    return written[2:] if written.startswith("r#") else written


_COMMENTS = None


def blank_comments(parser, src: bytes, tree) -> tuple[bytes, object]:
    """The source with every comment replaced by spaces (its newlines kept, so no byte and no line moves), parsed again.
    A comment is not a token: it only separates the tokens beside it, so it must change nothing ripple reads (a
    comment between the attributes of an item and the item, between the parts of a path, or inside a std macro's
    arguments would otherwise move a node of the tree and with it a name or a reason)."""
    global _COMMENTS
    from tree_sitter import Query, QueryCursor

    if _COMMENTS is None:
        _COMMENTS = Query(parser.language, "[(line_comment) (block_comment)] @c")
    nodes = QueryCursor(_COMMENTS).captures(tree.root_node).get("c", [])
    if not nodes:
        return src, tree
    blank = bytearray(src)
    for node in nodes:
        for i in range(node.start_byte, node.end_byte):
            if blank[i] not in (10, 13):
                blank[i] = 32
    blanked = bytes(blank)
    return blanked, parser.parse(blanked)


_TOOLS = None


def tool_names_in(root, src: bytes) -> frozenset[str]:
    """The names of TOOL_ATTRS that a file declares as a `mod`, or has in a `use` (as a segment, a name or an alias) or an `extern crate`."""
    global _TOOLS
    from tree_sitter import Query, QueryCursor

    if _TOOLS is None:
        names = " ".join(f'"{n}" "r#{n}"' for n in sorted(TOOL_ATTRS))
        _TOOLS = Query(load_parser().language, f"((identifier) @t (#any-of? @t {names}))")
    found = set()
    for node in QueryCursor(_TOOLS).captures(root).get("t", []):
        up = node.parent
        while up is not None and up.type in ("scoped_identifier", "use_as_clause", "scoped_use_list", "use_list", "use_wildcard"):
            up = up.parent
        if up is not None and up.type in ("mod_item", "extern_crate_declaration", "use_declaration"):
            found.add(name_text(src, node))
    return frozenset(found)


# ---- the definitions and scopes of a crate


@dataclass(eq=False)
class Def:
    kind: str  # fn struct enum union trait type const static mod macro variant tuple_struct unit_struct method decl extern_crate foreign
    name: str
    key: tuple  # (file, start byte) of its node: the same item read in two crates has one key
    node: object
    scope: Scope
    extra: dict = field(default_factory=dict)


@dataclass(eq=False)
class Scope:
    """What a module or a block declares: its items, its imports and its globs."""

    crate: CrateCtx
    module: Module | None  # None for a block, which is read through `home`
    items: dict[str, list[Def]] = field(default_factory=lambda: defaultdict(list))
    uses: dict[str, list[list[str]]] = field(default_factory=lambda: defaultdict(list))  # name -> [path segments]
    globs: list[list[str]] = field(default_factory=list)
    use_vis: dict[str, list[tuple]] = field(default_factory=lambda: defaultdict(list))  # name -> the visibility of each of its imports
    glob_vis: list[tuple] = field(default_factory=list)  # the visibility of each glob
    use_cfg: dict[str, list[bool]] = field(default_factory=lambda: defaultdict(list))  # name -> whether each of its imports is under a #[cfg]
    glob_cfg: list[bool] = field(default_factory=list)  # whether each glob is under a #[cfg]
    anonymous: bool = False  # `use Trait as _`: a trait this scope has in scope without a name
    macro_calls: list[tuple[str, bool]] = field(default_factory=list)  # (last segment, path starts at std) of each macro invoked as an item or a statement
    attribute_macro: bool = False  # an attribute macro or a derive that is not the standard library's is written on an item here
    item_macros: bool = False  # a macro invoked here may generate names (set once every crate is collected: see Index.mark_macros)
    rewritten: bool = False  # inside an item under an attribute macro, which may have rewritten all of it
    macro_use_mod: bool = False  # a block that holds a `#[macro_use] mod`, whose macro_rules ripple does not read: any macro name used here may be the repo's

    @property
    def may_make(self) -> bool:
        """Whether a name that nothing here declares may still have been generated: by a macro invoked here, or an attribute macro."""
        return self.item_macros
    parent: Scope | None = None  # a module's parent module (what `super` is)
    outer: Scope | None = None  # a block's enclosing scope
    home: Scope | None = None  # the module scope a block is in (what `self` is)
    mod_def: Def | None = None

    @property
    def module_scope(self) -> Scope:
        return self.home if self.home is not None else self


class CrateCtx:
    def __init__(self, crate: Crate, modules: list[Module], index: int):
        self.crate = crate
        self.modules = modules
        self.index = index
        self.scopes: dict[int, Scope] = {}
        self.root: Scope | None = None
        self.root_def: Def | None = None
        self.blocks: dict[tuple[str, int], Scope] = {}  # (file, start byte of a block) -> the scope of a block that declares items
        self.externs: dict[str, tuple] | None = None
        self.macro_use_extern = False  # `#[macro_use] extern crate x;`: macros of a crate ripple has not read are in scope
        self.prelude_import = False  # `#[prelude_import] use m::*;` (nightly) somewhere: the crate's standard prelude is a module of its own, whose `use`s no scope shows
        self.macro_sites: list[tuple[Scope, str, bool, str, int]] = []  # (scope, macro, path starts at std, file, line) of each macro invoked where it may generate items
        self.attr_sites: list[tuple[str, int, str] | TrustSite] = []  # (file, line, what) of each attribute macro or derive that may generate items, in source order (see attr_reasons)
        self.trust_sites: list[TrustSite] = []  # each function under trusted attributes only, until the repo proves them (Index.decide_trust)
        self.block_mods: list[tuple[str, int]] = []  # (file, line) of each `mod` written in a block, whose items ripple does not read: any impl in one applies to the whole crate
        self.taint: list[str] = []  # why the impls ripple read may not be all of this crate's (see Index.mark_macros)
        self.pending_derives: list[tuple[Scope, tuple[str, ...], str, int]] = []  # (scope, names, file, line) of each standard derive written by its bare name: another macro if something replaces the name (Index.settle_derives)
        self.derive_replaced: dict[int, str] = {}  # index into pending_derives -> the name of the derive that something replaces (Index.derive_step)
        self.pending_heads: list[tuple[Scope, tuple[str, ...], str, int]] = []  # (scope, attributes, file, line) of each on the target of a derive that is not a standard one
        self.use_names: set[str] = set()  # every name a `use` of the crate gives
        self.tools: frozenset[str] = frozenset()  # the names of TOOL_ATTRS that something of the crate declares, imports or depends on (Index.shadowed_tools)
        self.outside_glob = False  # a glob import that may bring a macro of a crate ripple has not read
        try:
            self.edition = int(crate.package.edition)
        except ValueError:
            self.edition = 2015

    def attr_reasons(self):
        """(file, line, what) of each attribute macro or derive that may generate items, in the order they were met. A function
        under trusted attributes is one only once refused, and then where it was met, as it would have been all along."""
        for site in self.attr_sites:
            if not isinstance(site, TrustSite):
                yield site
            elif site.refused is not None and not site.nested:
                yield site.entry


@dataclass(eq=False)
class TrustSite:
    """An item under attributes that are what they look like only if the repo proves it (Index.decide_trust): the attributes of
    TRUSTED_ATTRS on a free function and no other attribute macro, and the standard library's own attribute macros
    (SHADOWABLE_ATTRS), which a `use` may have replaced. It is read as if they were what they look like; a refusal reads it
    all as it is read under any attribute macro."""

    scope: Scope
    item: object
    file: str
    line: int
    heads: tuple[str, ...]  # the trusted attributes, as written
    shadow: tuple[str, ...]  # the standard library's attribute macros, as written
    name: str | None
    src: bytes
    refused: str | None = None  # why the repo does not prove them
    nested: bool = False  # refused only for being inside an item that is: what its attributes say is then no reason of its own

    @property
    def entry(self) -> tuple[str, int, str]:
        """What CrateCtx.attr_sites says of it once refused: what a macro there would have said."""
        macro = attrs_of(self.item, self.src, self.scope.crate.tools, trust=False, shadow=MACRO if self.shadow else BUILTIN)
        return attr_site(macro, self.file, self.line, self.item.type.replace("_item", "").replace("_", " "))


@dataclass(eq=False)
class ImplSpec:
    scope: Scope
    module: Module
    node: object
    src: bytes
    generics: tuple[str, ...]
    owner_id: str | None  # the id of the function an impl inside a body is in
    tname: str = "?"
    trait_plain: str = ""
    trait_text: str = ""
    self_target: tuple | None = None
    trait_target: tuple | None = None
    methods: dict[str, list[Def]] = field(default_factory=dict)  # name -> its definitions (several: cfg twins in one impl)
    consts: dict[str, list[Def]] = field(default_factory=dict)  # name -> the associated consts it defines
    cfg: bool = False  # the impl is under a #[cfg]
    rewritten: bool = False  # the impl is under an attribute macro, or inside an item that is
    macro_body: str | None = None  # a macro invoked in the body, which may define functions: its name
    via_alias: bool = False  # the self type is written through a type alias
    lost: str | None = None  # why the header could not be resolved (set when it names a path ripple could not follow)


@dataclass(eq=False)
class BodySpec:
    """Code whose calls are read: a function, or a const, static or item macro (which belongs to the module)."""

    caller: tuple  # key of the symbol that makes the calls; (file, -1) is the file's module
    node: object
    scope: Scope
    module: Module
    src: bytes
    impl: ImplSpec | None = None
    trait: Def | None = None
    generics: tuple[str, ...] = ()
    rewritten: bool = False  # under an attribute macro: every call in it is unresolved


@dataclass
class Body:
    scope: Scope
    ctx: CrateCtx
    src: bytes
    impl: ImplSpec | None
    trait: Def | None
    generics: frozenset
    locals: frozenset
    rewritten: bool = False


def path_segs(node, src: bytes) -> list[str]:
    """The segments of a path as written (`a::b::c`), generic arguments dropped. A path that starts with `::`
    starts with the segment "::"."""
    if node is None:
        return []
    t = node.type
    if t in ("scoped_identifier", "scoped_type_identifier"):
        path, name = node.child_by_field_name("path"), node.child_by_field_name("name")
        if path is not None:
            head = path_segs(path, src)
        else:
            head = ["::"] if node.children and node.children[0].type == "::" else []
        return head + [name_text(src, name)]
    if t in ("generic_type", "generic_type_with_turbofish"):
        return path_segs(node.child_by_field_name("type"), src)
    return [name_text(src, node)]


def written(segs: list[str]) -> str:
    return "::" + "::".join(segs[1:]) if segs and segs[0] == "::" else "::".join(segs)


def pattern_names(pattern, src: bytes) -> list[str]:
    """The names a pattern binds: its identifiers, except the path of a struct or variant pattern."""
    out: list[str] = []
    if pattern is None:
        return out
    stack = [pattern]
    while stack:
        n = stack.pop()
        if n.type in ("identifier", "shorthand_field_identifier"):
            parent = n.parent
            if n.type == "identifier" and parent is not None and parent.type in ("tuple_struct_pattern", "struct_pattern", "scoped_identifier"):
                field_name = next((parent.field_name_for_child(i) for i, c in enumerate(parent.children) if c.id == n.id), None)
                if field_name in ("type", "path", "name"):
                    continue
            out.append(name_text(src, n))
        elif n.type == "match_pattern":
            stack.extend(n.named_children[:1])  # the pattern, not its `if` guard
        else:
            stack.extend(n.named_children)
    return out


def scan_locals(root, src: bytes) -> frozenset:
    """Every name a function (or an expression) binds anywhere: parameters, lets, closure parameters, loop and
    match patterns. A single-segment call of one of them is a call of a local value, wherever in the body it is
    bound: that costs a few calls of a same-named function that is not shadowed yet, and never makes a wrong edge."""
    names: set[str] = set()
    stack = [root]
    while stack:
        n = stack.pop()
        t = n.type
        if n is not root and t in NESTED_ITEMS:
            continue
        if t == "macro_invocation":
            continue
        if t in ("let_declaration", "parameter", "let_condition", "for_expression"):
            names.update(pattern_names(n.child_by_field_name("pattern"), src))
        elif t == "match_arm":
            names.update(pattern_names(n.child_by_field_name("pattern"), src))
        elif t == "closure_parameters":
            for child in n.named_children:
                if child.type != "parameter":
                    names.update(pattern_names(child, src))
        stack.extend(n.named_children)
    return frozenset(names)


PUBLIC = ("pub",)
PRIVATE = ("self",)


def tail_expression(node) -> bool:
    """Whether a macro invocation is the last expression of a block, written with parentheses or brackets and no
    semicolon: it expands to an expression, which defines no name for the block (a `{ }` one is a statement)."""
    parent = node.parent
    if parent is None or parent.type != "block" or node.next_named_sibling is not None:
        return False
    tree = next((c for c in node.named_children if c.type == "token_tree"), None)
    return tree is None or not tree.children or tree.children[0].type != "{"


def statement_macro(node):
    """The macro invocation a statement of a block (or an item of a module) is made of (`bind!(x);`), or None: a
    macro in expression position, `let x = m!();` or the tail of a block, may expand to no item and no `let`."""
    if node.type == "macro_invocation":
        return None if tail_expression(node) else node
    if node.type == "expression_statement" and len(node.named_children) == 1 and node.named_children[0].type == "macro_invocation":
        return node.named_children[0]
    return None


def visibility(node, src: bytes) -> tuple:
    """How visible an item or a `use` is: ("pub",), ("crate",), ("super",), ("self",) (no modifier is private,
    which is `pub(self)`), or ("in", path segments) for `pub(in path)`."""
    modifier = next((c for c in node.children if c.type == "visibility_modifier"), None)
    return parse_visibility(text(src, modifier)) if modifier is not None else PRIVATE


def parse_visibility(written: str) -> tuple:
    flat = re.sub(r"\s*::\s*", "::", " ".join(written.split()))
    if flat == "pub":
        return PUBLIC
    kind = re.fullmatch(r"pub ?\( ?(crate|super|self) ?\)", flat)
    if kind:
        return (kind.group(1),)
    path = re.fullmatch(r"pub ?\( ?in ([A-Za-z_0-9:]+) ?\)", flat)
    return ("in", tuple(s for s in path.group(1).split("::") if s)) if path else ("in", ())


class Attrs:
    """What the attributes written before an item say that matters here."""

    __slots__ = ("cfg", "macro", "macro_use", "proc_macro", "derives", "unknown_derive", "std_derives", "what", "heads", "trust", "shadow")

    def __init__(self) -> None:
        self.cfg = False  # a #[cfg] or #[cfg_attr]: the item may not exist (ripple evaluates no condition)
        self.macro = False  # an attribute that is not on the built-in list: an attribute macro, which may rewrite the item
        self.macro_use = False
        self.proc_macro = False  # the function defines a macro (`#[proc_macro]`): its name is a macro's
        self.derives: list[str] = []
        self.unknown_derive = False  # a derive that is not one of the standard library's: it may generate any item
        self.std_derives: list[str] = []  # the derives written as a standard one's bare name (`Debug`), which a use, a macro_use extern crate or a repo macro of that name replaces
        self.what = ""  # the first attribute macro or derive of that kind, as written
        self.heads: list[str] = []  # attributes that are one plain name and not built in: an attribute macro, or the helper of a derive
        self.trust: list[str] = []  # attributes of TRUSTED_ATTRS, as written, that no attribute macro beside them or the item they are on rules out: trusted once the repo proves them
        self.shadow: list[str] = []  # attributes of SHADOWABLE_ATTRS, as written: the standard library's, unless the repo shows another macro of that name in scope

    @property
    def generates(self) -> bool:
        return self.macro or self.unknown_derive or bool(self.heads)


def _token_groups(tree) -> list[list]:
    """The tokens of a parenthesised token tree, split at its top-level commas."""
    groups: list[list] = [[]]
    for child in tree.children[1:-1]:
        if child.type == ",":
            groups.append([])
        else:
            groups[-1].append(child)
    return groups


# What to make of an attribute of SHADOWABLE_ATTRS: queue it (Attrs.shadow), read it as an attribute macro, or as the built-in
QUEUE, MACRO, BUILTIN = "queue", "macro", "builtin"


def _classify_attribute(head: str, args, src: bytes, out: Attrs, tools: frozenset, trust: bool = True, shadow: str = QUEUE) -> None:
    first = head.split("::")[0]
    if head == "cfg":
        out.cfg = True
    elif head == "cfg_attr":
        out.cfg = True
        if args is not None:  # `cfg_attr(condition, attribute, ..)`: what it adds is as unknown as the condition
            for group in _token_groups(args)[1:]:
                path = "".join(text(src, c) for c in group if c.type in ("identifier", "::", "self", "crate", "super"))
                tree = next((c for c in group if c.type == "token_tree"), None)
                if path:
                    _classify_attribute(path, tree, src, out, tools, trust, shadow)
    elif head == "derive":
        for group in _token_groups(args) if args is not None else ():
            written = "".join(text(src, c) for c in group)
            name = written.split("::")[-1]
            if name.startswith("r#"):
                name = name[2:]
            if name:
                out.derives.append(name)
            if name not in STD_DERIVES or ("::" in written and written.split("::")[0] not in ("std", "core", "alloc")):
                out.unknown_derive = True
                out.what = out.what or f"the derive `{written}`"
            elif "::" not in written:
                out.std_derives.append(name)  # the standard derive of that name, unless something replaces it (Index.settle_derives)
        _shadowable(head, out, shadow)
    elif head == "macro_use":
        out.macro_use = True
    elif head in ("proc_macro", "proc_macro_attribute"):
        out.proc_macro = True
    elif trust and head in TRUSTED_ATTRS and not (TRUSTED_ATTRS[head] == "serial_test" and args is not None and "inner_attrs" in text(src, args).lower()):
        out.trust.append(head)  # serial's `inner_attrs = [..]` (any case) moves the body into a function that carries attributes of any kind
    elif head in SHADOWABLE_ATTRS:
        _shadowable(head, out, shadow)
    elif first not in BUILTIN_ATTRS or _tool_is_macro(head, first, tools):
        if "::" in head or head in TOOL_ATTRS:  # a path is no helper of a derive, and a bare tool name builds only as a macro
            out.macro = True
            out.what = out.what or f"the attribute `#[{head}]`"
        else:
            out.heads.append(head)


def _tool_is_macro(head: str, first: str, tools: frozenset) -> bool:
    """Whether a head whose first segment is on BUILTIN_ATTRS is a macro anyway. `rustfmt::skip` is inert unless something of the
    tool's name is in the crate (`tools`). Any other path that starts at a name of the list builds only through a module of that
    name (`mod doc { pub use m::foo; }` then `#[doc::foo]`), and a bare tool name only as a macro: neither is an attribute of the compiler."""
    if "::" not in head:
        return head in TOOL_ATTRS
    return first not in TOOL_ATTRS or first in tools


def _shadowable(head: str, out: Attrs, shadow: str) -> None:
    if shadow == QUEUE:
        out.shadow.append(head)  # the built-in, unless the repo shows another macro of that name in scope (Index.shadow_problem)
    elif shadow == MACRO:
        out.macro = True
        out.what = out.what or f"the attribute `#[{head}]`"


def read_attributes(attributes: list, src: bytes, tools: frozenset, trust: bool = True, shadow: str = QUEUE) -> Attrs:
    """What attributes say. With `trust` the three of TRUSTED_ATTRS are set apart (Attrs.trust) instead of read as attribute
    macros, and the standard library's attribute macros (SHADOWABLE_ATTRS) are set apart too (Attrs.shadow, by `shadow`),
    instead of read as built in; the caller decides whether the item they are on lets them stay apart (attrs_of). `tools` is
    the crate's CrateCtx.tools: the tool attributes (TOOL_ATTRS) that are macros there."""
    out = Attrs()
    for item in attributes:
        attribute = next((c for c in item.named_children if c.type == "attribute"), None)
        path = attribute.named_children[0] if attribute is not None and attribute.named_children else None
        if path is not None:
            _classify_attribute("".join(text(src, path).split()), attribute.child_by_field_name("arguments"), src, out, tools, trust, shadow)
    return out


def attr_site(attrs: Attrs, file: str, line: int, what: str) -> tuple[str, int, str]:
    """What CrateCtx.attr_sites says of an item under an attribute macro or a derive that is not the standard library's."""
    return (file, line, f"{attrs.what} on a {what} (an attribute macro or a derive that is not the standard library's)")


def trusted_target(item) -> bool:
    """Whether the attributes of TRUSTED_ATTRS are on what they are trusted on: a function of a module or of a block. Not an
    impl's or a trait's method (serial_test drops its `self` and puts a function beside it), not a module (`#[serial]`
    rewrites the tests of one), not a type. (attrs_of also rules out serial_test's on an `async fn`.)"""
    if item.type != "function_item":
        return False
    parent = item.parent
    return not (parent is not None and parent.type == "declaration_list" and parent.parent is not None and parent.parent.type in ("impl_item", "trait_item"))


def is_async(item) -> bool:
    modifiers = next((c for c in item.children if c.type == "function_modifiers"), None)
    return modifiers is not None and any(c.type == "async" for c in modifiers.children)


def queueable(item) -> bool:
    """Whether the attributes of SHADOWABLE_ATTRS are on what each can be written on, and what Index.collect_items queues: a
    function, a type or a static of a module or of a block. Written anywhere else (the method of an impl, a statement, a
    module) the built-in would be an error, so only another macro of that name can be there: it is read as one."""
    return trusted_target(item) or item.type in ("struct_item", "enum_item", "union_item", "static_item")


def attrs_of(item, src: bytes, tools: frozenset, trust: bool = True, shadow: str = QUEUE) -> Attrs:
    """The attributes written directly before an item (or a statement, or an impl item). A single name that is not built in
    is an attribute macro, except beside a derive that is not a standard one, where it may be that derive's helper (the
    caller settles it, once every `use` of the crate is known).

    The attributes of TRUSTED_ATTRS are left out of that (they are in `.trust`) only when nothing rules them out: the item is
    a free function (trusted_target), none is serial_test's on an `async fn` (whose body moves into a function named
    `_<name>_internal`, beside the function or inside it by version), and no other attribute macro is on the item, which
    could rewrite what these see. Otherwise they are read as attribute macros, as every attribute that is not built in is.
    `trust=False` reads them that way always: what a site not proven by the repo is.

    The standard library's own attribute macros (`test`, `bench`, `derive`, `global_allocator`) are in `.shadow` on an item that
    can carry them (queueable), else read as attribute macros; `shadow=BUILTIN` reads them as the built-ins they are unless a
    `use` says otherwise, which is what an item inside one that is rewritten already is, and `shadow=MACRO` as macros."""
    attributes = rs_modules._attributes(item)
    out = read_attributes(attributes, src, tools, trust, shadow)
    off = bool(out.trust) and bool(out.macro or out.heads or out.unknown_derive or not trusted_target(item) or (is_async(item) and any(TRUSTED_ATTRS[h] == "serial_test" for h in out.trust)))
    elsewhere = bool(out.shadow) and not queueable(item)
    if off or elsewhere:
        out = read_attributes(attributes, src, tools, trust=trust and not off, shadow=MACRO if elsewhere else shadow)
    if out.heads and not (out.unknown_derive and item.type in ("struct_item", "enum_item", "union_item")):
        out.macro = True
        out.what = out.what or f"the attribute `#[{out.heads[0]}]`"
    return out


def inner_attrs_of(container, src: bytes, tools: frozenset) -> Attrs:
    """The inner attributes (`#![cfg(..)]`) a module's file or block begins with."""
    return read_attributes([c for c in container.named_children if c.type == "inner_attribute_item"], src, tools, trust=False, shadow=BUILTIN)


def squash(written: str) -> str:
    """A type or an argument list written as one line with no spacing that a comment or a line break could change."""
    flat = " ".join(written.split())
    flat = re.sub(r"\s*,\s*", ", ", flat)
    flat = re.sub(r"\s*([>)\]])", r"\1", flat)
    flat = re.sub(r"([<(\[])\s*", r"\1", flat)
    return re.sub(r"\s*::\s*", "::", flat)


def generic_names(node, src: bytes) -> tuple[str, ...]:
    """The type and const parameters an item declares (`impl<T, const N: usize>` gives T and N)."""
    params = node.child_by_field_name("type_parameters")
    out = []
    for p in params.named_children if params is not None else ():
        if p.type in ("type_parameter", "constrained_type_parameter", "const_parameter", "optional_type_parameter"):
            name = p.child_by_field_name("name") or p.child_by_field_name("left")
            if name is None and p.named_children:
                name = p.named_children[0]
            if name is not None:
                out.append(name_text(src, name))
    return tuple(out)


# ---- what a call can be proven to be: ("edge", callee key, override keys), ("external", why), ("ctor",), ("unresolved", why)

CTOR = ("ctor",)


def edge(callee: Def, overrides=()) -> tuple:
    return ("edge", callee.key, tuple(o.key for o in overrides))


def ext_step(why: str, seg: str) -> str:
    """The outside-the-repo result of a path one segment further: for the standard library and the prelude it keeps the
    path it resolved to (`std|std::fmt::Display::fmt`), which is what says whether a segment is std's own type."""
    base, bar, _ = why.partition("|")
    return f"{why}::{seg}" if bar and base in ("std", "prelude", "primitive") else why


def external(why: str) -> tuple:
    return ("external", why)


def unresolved(why: str) -> tuple:
    return ("unresolved", why)


def _key(result: tuple) -> tuple:
    """What makes two lookup results the same one."""
    kind, x = result
    if kind == "def" or kind == "crate":
        return (kind, id(x))
    if kind == "assoc":
        return (kind, id(x[0]), x[1])
    return (kind, x)


def _unique(results: list[tuple]) -> list[tuple]:
    seen, out = set(), []
    for r in results:
        k = _key(r)
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def in_namespace(d: Def, ns: str) -> bool:
    if ns == "type":
        return d.kind in TYPE_KINDS
    if ns == "value":
        return d.kind in VALUE_KINDS
    return d.kind == "macro"


def written_args_of(node, src: bytes) -> tuple | None:
    """The type arguments a written type carries, lifetimes left out, each as one text: () for a path with none, None for
    a type that is no path."""
    if node is None:
        return None
    if node.type in ("generic_type", "generic_type_with_turbofish"):
        args = node.child_by_field_name("type_arguments")
        return tuple(squash(text(src, a)) for a in args.named_children if a.type != "lifetime") if args is not None else ()
    if node.type in ("type_identifier", "scoped_type_identifier", "identifier", "scoped_identifier"):
        return ()
    return None


def written_args(node, src: bytes) -> tuple | None:
    """The type arguments a call or an alias writes after the type (`G::<u16>::f`, `type A = G<u16>`); None when it writes none."""
    return written_args_of(node, src) if node is not None and node.type in ("generic_type", "generic_type_with_turbofish") else None


def concrete_args(args: tuple) -> bool:
    """Whether type arguments name only primitive types (`u8`, `(u8, bool)`, `[u8; 4]`), which no name in scope can change."""
    return all(re.fullmatch(r"[A-Za-z0-9_,()\[\];&* ]+", a) and all(w in PRIMITIVES for w in re.findall(r"[A-Za-z_][A-Za-z_0-9]*", a)) for a in args)


class Index:
    """Every crate of the repo read into scopes and items, with the impls indexed, ready to resolve calls."""

    def __init__(self, trees: list[tuple[Crate, rs_modules.Tree]]):
        self.ctxs: list[CrateCtx] = []
        self.lib_ctx: dict[str, CrateCtx] = {}  # package dir -> the context of its library
        self.inherent: dict[Def, dict[str, list[Def]]] = defaultdict(lambda: defaultdict(list))
        self.trait_impls: dict[Def, list[ImplSpec]] = defaultdict(list)  # type def -> its trait impls
        self.impls_of: dict[Def, list[ImplSpec]] = defaultdict(list)  # trait def -> every impl of it
        self.trait_decl: dict[Def, dict[str, Def]] = {}
        self.supertraits: dict[Def, tuple[object, Scope]] = {}
        self.derives: dict[Def, set[str]] = defaultdict(set)
        self.repo_macros: set[str] = set()
        self.trait_fn_names: set[str] = set()  # every function name some repo trait declares
        self.blanket_fns: set[str] = set()  # the functions of impls for a generic type (`impl<T: X> Tr for T`)
        self.impl_fn_names: set[str] = set()  # the functions of every trait impl in the repo
        self.foreign_impl_fns: set[str] = set()  # the functions of trait impls for a type from outside the repo, or a generic one
        self.wild_traits: set[Def] = set()  # repo traits with an impl for a generic type, a reference, a tuple or a trait object: any type may be an impl of it
        self.impl_specs: list[ImplSpec] = []
        self.trait_counts: Counter = Counter()
        self.bodies: list[BodySpec] = []
        self.raw: dict[tuple, tuple] = {}  # key -> (base id, kind, file, node): every symbol, once
        self.final: dict[tuple, str] = {}
        self.sites: dict[tuple, dict[int, tuple]] = defaultdict(dict)
        self.quoted = 0
        self.cuts = 0
        self.cache: dict[tuple, list] = {}
        self.reach_cache: dict[int, list] = {}
        # libraries first, so a file several crates share is read in its library's context first
        order = {"lib": 0, "bin": 1}
        for crate, tree in sorted((ct for ct in trees if ct[1].modules), key=lambda ct: order.get(ct[0].kind, 2)):
            ctx = CrateCtx(crate, tree.modules, len(self.ctxs))
            self.ctxs.append(ctx)
            if crate.kind == "lib":
                self.lib_ctx[crate.package.dir] = ctx
        named: dict[str, frozenset[str]] = {}  # file -> the tool names it declares or imports, for a file that several crates read
        for ctx in self.ctxs:
            ctx.tools = self.shadowed_tools(ctx, named)
            self.collect(ctx)
        self.mark_macros()
        done = 0
        while done < len(self.impl_specs):  # an impl inside a function body adds one while it is read
            spec = self.impl_specs[done]
            done += 1
            self.resolve_impl(spec)
            self.register_impl(spec)
        self.mark_macros()  # the bodies of the impls added scopes (and macros) of their own
        self.cache.clear()
        for spec in self.impl_specs:
            if spec.trait_plain:
                self.impl_fn_names |= set(spec.methods)
                if spec.self_target is None or spec.self_target[0] in ("ext", "generic"):
                    self.foreign_impl_fns |= set(spec.methods)
            if spec.self_target is not None and spec.self_target[0] == "generic" and spec.trait_target is not None:
                self.blanket_fns |= set(spec.methods)
                if spec.trait_target[0] == "def":
                    self.blanket_fns |= set(self.trait_decl.get(spec.trait_target[1], {}))
            if spec.trait_target is not None and spec.trait_target[0] == "def" and (spec.self_target is None or spec.self_target[0] == "generic"):
                self.wild_traits.add(spec.trait_target[1])

    @staticmethod
    def shadowed_tools(ctx: CrateCtx, named: dict[str, frozenset[str]]) -> frozenset[str]:
        """The names of TOOL_ATTRS (`rustfmt`, `clippy`, `diagnostic`) that something of the crate declares, imports, aliases or depends
        on, anywhere: a `mod`, a `use` with the name in it, an `extern crate` of it (or aliased to it), a dependency key of the
        package, or, for a binary or a test of a package with a library, that library's name (it is in their extern prelude). Each makes
        `#[rustfmt::skip]` another macro in rustc's eyes (built with local proc-macro crates), wherever the attribute is written
        in the crate, since the rule is the simple one: a head of such a name is a macro like any other when this set holds its first segment."""
        found = {name for name in ctx.crate.package.deps if name in TOOL_ATTRS}
        if ctx.crate.kind != "lib" and ctx.crate.package.lib_name in TOOL_ATTRS:
            found.add(ctx.crate.package.lib_name)
        for m in ctx.modules:
            if m.node.type == "source_file":
                if m.file not in named:
                    named[m.file] = tool_names_in(m.node, m.src)
                found |= named[m.file]
        return frozenset(found)

    # ---- ids
    def make_id(self, m: Module, owner_id: str | None, *names: str) -> str:
        """The id of an item: the module's, then its path inside the file (`tests::helper`); inside a function body
        it continues the function's id (`outer::inner`)."""
        if owner_id:
            return owner_id + "::" + "::".join(names)
        inner = "::".join([*m.inline, *names])
        base = module_id(m.file)
        return f"{base}:{inner}" if inner else base

    def claim(self, key: tuple, base: str, kind: str, file: str, node) -> None:
        self.raw.setdefault(key, (base, kind, file, node))

    # ---- collecting items
    def collect(self, ctx: CrateCtx) -> None:
        for m in ctx.modules:
            scope = Scope(ctx, m)
            ctx.scopes[id(m)] = scope
            if not m.path:
                ctx.root = scope
                # `#[prelude_import]` is E0658 without `#![feature(prelude_import)]` in the crate root (also behind `cfg_attr` and in a list), and
                # ripple sees the attribute only on an item it parses, not in a macro body, an include!d file or a `mod` written in a block: so the
                # gate is what is looked for, in the text of the root (comments blanked), where a syntax error cannot hide it
                if PRELUDE_IMPORT.search(m.src):
                    ctx.prelude_import = True
        if ctx.root is None:
            ctx.prelude_import = True  # no root to read the gate in
        if ctx.root is not None:
            ctx.root_def = Def("mod", "crate", (ctx.crate.root, -1), None, ctx.root, {"scope": ctx.root, "vis": PUBLIC})
        for m in ctx.modules:
            scope = ctx.scopes[id(m)]
            if m.parent is not None:
                parent = ctx.scopes[id(m.parent)]
                scope.parent = parent
                declared = attrs_of(m.decl, m.parent.src, ctx.tools) if m.decl is not None else Attrs()
                inner = inner_attrs_of(m.node, m.src, ctx.tools)
                extra = {"scope": scope, "vis": parse_visibility(m.vis) if m.vis else PRIVATE}
                if declared.cfg or inner.cfg:
                    extra["cfg"] = True
                scope.rewritten = parent.rewritten or declared.macro
                if scope.rewritten:
                    extra["rw"] = True
                mod = Def("mod", m.path[-1], (m.file, m.node.start_byte, "mod"), m.node, parent, extra)
                parent.items[m.path[-1]].append(mod)
                scope.mod_def = mod
                scope.attribute_macro = declared.macro  # the module may have been rewritten: names it lacks may be made
            self.collect_items(scope, m.node, m, ctx, rw=scope.rewritten)

    def note_attrs(self, ctx: CrateCtx, scope: Scope, attrs: Attrs, file: str, line: int, what: str) -> None:
        """An attribute macro or a derive that is not the standard library's may generate items beside what it is written on."""
        if attrs.macro or attrs.unknown_derive:
            scope.attribute_macro = True  # an attribute macro may rewrite the item and generate any other beside it, and a derive generate any item beside it
        if attrs.macro or attrs.unknown_derive:  # a derive generates impls (and any item), but rewrites nothing
            ctx.attr_sites.append(attr_site(attrs, file, line, what))
        if attrs.std_derives:
            ctx.pending_derives.append((scope, tuple(attrs.std_derives), file, line))

    def collect_items(self, scope: Scope, container, m: Module, ctx: CrateCtx, owner_id: str | None = None, block: bool = False, rw: bool = False) -> None:
        """The items a container (a module's body or a block) declares, into the scope. `rw` is a container inside an
        item under an attribute macro (which may have rewritten all of it)."""
        src = m.src
        for item in container.named_children:
            t = item.type
            name_node = item.child_by_field_name("name")
            name = name_text(src, name_node) if name_node is not None else None
            key = (m.file, item.start_byte)
            vis = visibility(item, src) if t in ITEM_NODES else PRIVATE
            attrs = attrs_of(item, src, ctx.tools) if t != "attribute_item" else Attrs()
            queued = bool(attrs.trust or attrs.shadow) and not rw  # attributes that are what they look like only once the repo proves it (Index.decide_trust)
            if (attrs.trust or attrs.shadow) and rw:
                attrs = attrs_of(item, src, ctx.tools, trust=False, shadow=BUILTIN)  # inside an item an attribute macro may have rewritten, they are read as one (a built-in is no matter)
            if "prelude_import" in attrs.heads:
                ctx.prelude_import = True
            if attrs.heads and not attrs.macro:
                # a name beside a derive that is not a standard one is that derive's helper, unless an attribute macro of
                # that name is in scope (decided once every `use` of the crate is known: see Index.mark_macros)
                ctx.pending_heads.append((scope, tuple(attrs.heads), m.file, item.start_point[0] + 1))
            facts = {"vis": vis}
            if attrs.cfg:
                facts["cfg"] = True
            if rw or attrs.macro or attrs.trust:
                facts["rw"] = True  # a trusted function keeps its name unresolved: old versions of both macro crates drop its `pub`
            elif attrs.heads:
                facts["heads"] = tuple(attrs.heads)
            if queued:  # a site that is refused is read as a macro there would be read (TrustSite.entry, Index.refuse_trust)
                site = TrustSite(scope, item, m.file, item.start_point[0] + 1, tuple(attrs.trust), tuple(attrs.shadow), name, src)
                ctx.attr_sites.append(site)
                ctx.trust_sites.append(site)
            if t != "macro_invocation" and t != "attribute_item":
                self.note_attrs(ctx, scope, attrs, m.file, item.start_point[0] + 1, t.replace("_item", "").replace("_", " "))
            if t == "function_item":
                sid = self.make_id(m, owner_id, name)
                d = Def("fn", name, key, item, scope, dict(facts))
                scope.items[name].append(d)
                if attrs.proc_macro:
                    self.repo_macros.add(name)
                self.claim(key, sid, "function", m.file, item)
                self.bodies.append(BodySpec(key, item, scope, m, src, None, None, generic_names(item, src), rw or attrs.macro))
                body = item.child_by_field_name("body")
                if body is not None:
                    self.collect_blocks(body, scope, m, ctx, sid, rw or attrs.macro)
            elif t in ("struct_item", "union_item"):
                body = item.child_by_field_name("body")
                d = Def("union" if t == "union_item" else "struct", name, key, item, scope, dict(facts))
                scope.items[name].append(d)
                if body is not None and body.type == "ordered_field_declaration_list":
                    # the constructor is as visible as the least visible of the fields
                    open_fields = all(any(c.type == "visibility_modifier" for c in f.children) for f in body.named_children if f.type == "ordered_field_declaration")
                    scope.items[name].append(Def("tuple_struct", name, key, item, scope, {**facts, "type": d, "vis": vis if open_fields else PRIVATE}))
                elif body is None:
                    scope.items[name].append(Def("unit_struct", name, key, item, scope, {**facts, "type": d}))
                self.claim(key, self.make_id(m, owner_id, name), "class", m.file, item)
                self.note_derives(d, attrs)
            elif t == "enum_item":
                d = Def("enum", name, key, item, scope, {**facts, "variants": {}})
                body = item.child_by_field_name("body")
                for v in body.named_children if body is not None else ():
                    if v.type == "enum_variant":
                        vn = name_text(src, v.child_by_field_name("name"))
                        d.extra["variants"][vn] = Def("variant", vn, (m.file, v.start_byte), v, scope, {"type": d})
                scope.items[name].append(d)
                self.claim(key, self.make_id(m, owner_id, name), "class", m.file, item)
                self.note_derives(d, attrs)
            elif t == "trait_item":
                d = Def("trait", name, key, item, scope, dict(facts))
                scope.items[name].append(d)
                self.claim(key, self.make_id(m, owner_id, name), "class", m.file, item)
                self.collect_trait(d, item, scope, m, ctx, owner_id, rw or attrs.macro)
            elif t == "type_item":
                scope.items[name].append(Def("type", name, key, item, scope, dict(facts)))
            elif t in ("const_item", "static_item"):
                scope.items[name].append(Def("const" if t == "const_item" else "static", name, key, item, scope, dict(facts)))
                if not block:
                    self.bodies.append(BodySpec((m.file, -1), item, scope, m, src, rewritten=rw or attrs.macro))
                    self.collect_blocks(item, scope, m, ctx, self.make_id(m, owner_id, name), rw or attrs.macro)
            elif t == "mod_item" and block:
                # a module declared in a block shadows the items of that name around it, and ripple does not read what is in it: a
                # path that starts at it is unresolved (Index._lookup). What it holds still acts outside it: an impl applies to the
                # whole crate (CrateCtx.block_mods taints the impls), and a `#[macro_use]` one leaves its macro_rules in scope
                # for the rest of the block (Scope.macro_use_mod)
                scope.items[name].append(Def("mod", name, key, item, scope, {**facts, "opaque": True}))
                ctx.block_mods.append((m.file, item.start_point[0] + 1))
                scope.macro_use_mod |= attrs.macro_use
            elif t == "macro_definition":
                self.repo_macros.add(name)
                scope.items[name].append(Def("macro", name, key, item, scope))
            elif t == "use_declaration":
                self.collect_use(scope, item.child_by_field_name("argument"), [], src, vis, attrs.cfg)
            elif t == "extern_crate_declaration":
                crate_name = name_text(src, name_node)
                alias = item.child_by_field_name("alias")
                scope.items[name_text(src, alias) if alias is not None else crate_name].append(Def("extern_crate", crate_name, key, item, scope, dict(facts)))
                if attrs.macro_use and crate_name not in ("std", "core", "alloc"):
                    ctx.macro_use_extern = True
            elif t == "foreign_mod_item":
                body = item.child_by_field_name("body")
                for f in body.named_children if body is not None else ():
                    fn = f.child_by_field_name("name")
                    if f.type == "function_signature_item" and fn is not None:
                        inner = attrs_of(f, src, ctx.tools)
                        scope.items[name_text(src, fn)].append(
                            Def("foreign", name_text(src, fn), (m.file, f.start_byte), f, scope, {"vis": visibility(f, src), **({"cfg": True} if attrs.cfg or inner.cfg else {}), **({"rw": True} if rw or attrs.macro or inner.macro else {})})
                        )
            elif t == "impl_item":
                self.collect_impl(scope, item, m, owner_id, attrs, rw or attrs.macro)
            elif t == "macro_invocation":
                if statement_macro(item) is not None:
                    self.note_macro(scope, item, src, ctx, m.file)
                if not block:
                    self.bodies.append(BodySpec((m.file, -1), item, scope, m, src, rewritten=rw))
                self.note_attrs(ctx, scope, attrs, m.file, item.start_point[0] + 1, "macro invocation")
            elif t == "expression_statement" and statement_macro(item) is not None:
                # `bind!(x);` in a body may expand to items and `let`s; at module level (where tree-sitter reads `m!(..);` as
                # a statement, and `m!{..}` as a macro) it may expand to any item
                self.note_macro(scope, statement_macro(item), src, ctx, m.file)
                if not block:
                    self.bodies.append(BodySpec((m.file, -1), item, scope, m, src, rewritten=rw))

    @staticmethod
    def note_macro(scope: Scope, node, src: bytes, ctx: CrateCtx | None = None, file: str = "", names: bool = True) -> None:
        """Record a macro invoked where it may define names (as an item, or as a statement of a block) and, wherever it
        is, where it may generate impls. `names` is False for a macro in an impl or a trait body, which defines
        members of that impl or trait and no name of the scope."""
        mnode = node.child_by_field_name("macro")
        if mnode is None:
            if names:
                scope.attribute_macro = True
            if ctx is not None:
                ctx.macro_sites.append((scope, "?", False, file, node.start_point[0] + 1))
            return
        segs = path_segs(mnode, src)
        qualified = len(segs) > 1 and segs[0] in ("std", "core", "alloc")
        if names:
            scope.macro_calls.append((segs[-1], qualified))
        if ctx is not None:
            ctx.macro_sites.append((scope, segs[-1], qualified, file, node.start_point[0] + 1))

    def mark_macros(self) -> None:
        """Whether a macro may generate names in each scope, once every macro_rules! of the repo is known: an attribute
        macro, or an invocation that is not of a standard macro known to define nothing (a repo macro of that name, or
        one a `use` brings in, is another macro). And whether it may generate impls: any such invocation, in any scope
        (an impl or a trait body, a block), any attribute macro, and any derive that is not a standard one, which may
        generate any item; each is a reason the impls of the crate that holds it may not be all ripple read."""
        self.decide_trust()
        for ctx in self.ctxs:
            ctx.taint = []
            self.settle_helpers(ctx)
        self.settle_derives()  # after every crate's scopes are flagged: whether `std` is the crate in a scope depends on them
        for ctx in self.ctxs:
            for i, name in sorted(ctx.derive_replaced.items()):
                ctx.taint.append(f"the derive `{name}` in {ctx.pending_derives[i][2]} may be another macro than the standard library's, which may generate impls")
            self.mark_item_macros(ctx)
            for scope, name, qualified, file, line in ctx.macro_sites:
                if not self.is_std_macro(scope, name, qualified, STD_PURE_MACROS | STD_NO_IMPL_MACROS):
                    ctx.taint.append(f"the macro `{name}!` invoked in {file} may generate impls")
            for file, line, what in ctx.attr_reasons():
                ctx.taint.append(f"{what} in {file} may generate impls")
            for file, line in ctx.block_mods:
                ctx.taint.append(f"the `mod` written in a block in {file} may hold impls, which ripple does not read there")
        for spec in self.impl_specs:
            if spec.lost:
                spec.scope.crate.taint.append(spec.lost)

    def mark_item_macros(self, ctx: CrateCtx) -> None:
        for scope in self.all_scopes(ctx):
            scope.item_macros = scope.attribute_macro or any(not self.is_std_macro(scope, name, qualified) for name, qualified in scope.macro_calls)

    # ---- trusted attribute macros
    def decide_trust(self) -> None:
        """Whether each function under trusted attributes only (CrateCtx.trust_sites) is under the published crates' macros: the
        package must depend on the crate as crates.io's (rs_modules._trust), and the attribute as written must name it here
        (`tokio` resolves to that dependency; `serial` is imported by `use serial_test::serial`). One that is not proven is
        read as under any attribute macro: its body is rewritten and its scope may hold names it generated. That can make
        a scope one in which a macro may make names (the `tokio` of another site there is no longer proven), so this runs
        until nothing changes. The sites of an impl's body are found after the first run, so it runs again then.

        The sites are collected as trusted and flipped when refused, rather than held back until decided, because proving one
        needs every scope of the crate read, the blocks inside the trusted functions included (their globs and macros
        decide what a name there can be)."""
        if not any(ctx.trust_sites for ctx in self.ctxs):
            return
        for ctx in self.ctxs:
            self.settle_helpers(ctx)
        while True:
            for ctx in self.ctxs:
                self.mark_item_macros(ctx)
            self.cache.clear()
            changed = self.derive_step()
            for ctx in self.ctxs:
                for site in ctx.trust_sites:
                    if site.refused is None:
                        why = self.trust_identity(ctx, site)
                        if why is not None:
                            self.refuse_trust(ctx, site, why)
                            changed = True
            if not changed:
                break
        self.cache.clear()

    def shadow_problem(self, ctx: CrateCtx, scope: Scope, name: str) -> str | None:
        """None when `#[name]` written in this scope is the standard library's (`test`, `bench`, `derive`, `global_allocator`), else
        why it may be another macro. Only these can make it another: a `use` that gives the name, in the scope or a block around
        it (a module's own uses only: a parent's reach a child by a glob, which rustc rejects as ambiguous with the prelude's
        macro, as it does a glob from outside the repo or a macro that makes one); a `#[macro_use] extern crate`, which puts a
        crate's macros in scope of every module; and a macro of the repo of that name (a `macro_rules!` attribute, still
        unstable, is the other). The forms were each built against a local proc-macro crate."""
        if name in self.repo_macros:
            return f"a macro of the repo is called {name}"
        if ctx.macro_use_extern:
            return f"a #[macro_use] extern crate may bring a macro called {name}"
        s: Scope | None = scope
        while s is not None:
            if name in s.uses:
                return f"a use gives the name {name} here"
            s = s.outer
        return None

    def trust_identity(self, ctx: CrateCtx, site: TrustSite) -> str | None:
        """None when every attribute of the site is the published crate's macro, else why one may not be."""
        for name in site.shadow:
            why = self.shadow_problem(ctx, site.scope, name)
            if why:
                return "name: " + why
        for head in site.heads:
            crate = TRUSTED_ATTRS[head]
            why = ctx.crate.package.trust.get(crate, "no verdict for this package")
            if why:
                return "manifest: " + why
            if ctx.edition < 2018:
                return "edition: " + EDITION
            if head == "serial":
                why = self.serial_imported(site.scope)
            elif head.startswith("::"):
                got = [self.externs(ctx).get(crate)]
                why = None if got == [("ext", "crate:" + crate)] else f"`::{crate}` is {got}"
            else:
                got = self.lookup(site.scope, crate, "type")
                why = None if got == [("ext", "crate:" + crate)] else f"`{crate}` here is {[(kind, getattr(x, 'name', x)) for kind, x in got][:2]}"
            if why:
                return "name: " + why
        return None

    def serial_imported(self, scope: Scope) -> str | None:
        """None when `serial` written as an attribute here is `serial_test::serial`: the nearest scope (a block, then the module,
        never the parent modules) that declares or imports the name imports it by exactly one `use serial_test::serial;` that
        is not under a `#[cfg]`, and `serial_test` there is the crate. A glob or a macro that may make names, met first, or a
        macro of the repo called serial, may be another."""
        if "serial" in self.repo_macros:
            return "a macro of the repo is called serial"
        s: Scope | None = scope
        while s is not None:
            if "serial" in s.uses:
                entries = s.uses["serial"]
                if len(entries) != 1 or entries[0] not in (["serial_test", "serial"], ["::", "serial_test", "serial"]):
                    return f"serial is imported as {entries}"
                if s.use_cfg["serial"][0]:
                    return "the use of serial is under a #[cfg]"
                if entries[0][0] == "::":
                    got = [self.externs(s.crate).get("serial_test")]
                else:
                    got = self.lookup(s, "serial_test", "type")
                return None if got == [("ext", "crate:serial_test")] else f"`serial_test` here is {got[:2]}"
            if s.globs:
                return "a glob import may bring another serial"
            if s.item_macros:
                return "a macro may define serial here"
            s = s.outer
        return "no use brings serial in"

    def refuse_trust(self, ctx: CrateCtx, site: TrustSite, why: str) -> None:
        """A site the repo does not prove: everything inside it is read as under an attribute macro, as it was before the rule.
        What that sets is what collect_items and the readers of an item set for one that is rewritten: the scope holds names
        the macro may have made, the name and everything declared inside are unresolved, and the attribute may generate impls
        (CrateCtx.attr_reasons). Sites inside it are inside an item that is rewritten, so they are refused with it."""
        site.refused = why
        site.scope.attribute_macro = True
        file, a, b = site.file, site.item.start_byte, site.item.end_byte
        for d in site.scope.items.get(site.name, ()):
            if d.key == (file, a):
                d.extra["rw"] = True  # a built-in that is another macro may have rewritten the item, as a macro there would

        def inside(f: str, start: int) -> bool:
            return f == file and a <= start < b

        for spec in self.bodies:
            if spec.scope.crate is ctx and inside(spec.module.file, spec.node.start_byte):
                spec.rewritten = True
        for (f, start), block in ctx.blocks.items():
            if inside(f, start):
                block.rewritten = True
                for defs in block.items.values():
                    for d in defs:
                        if d.kind != "macro":
                            d.extra["rw"] = True
        for spec in self.impl_specs:
            if spec.scope.crate is ctx and inside(spec.module.file, spec.node.start_byte):
                spec.rewritten = True
                for defs in (*spec.methods.values(), *spec.consts.values()):
                    for d in defs:
                        d.extra["rw"] = True
        for d, decls in self.trait_decl.items():
            if d.scope.crate is ctx and inside(d.key[0], d.key[1]):
                for decl in decls.values():
                    decl.extra["rw"] = True
        for other in ctx.trust_sites:
            if other.refused is None and other is not site and inside(other.file, other.item.start_byte):
                if other.heads:
                    self.refuse_trust(ctx, other, "inside a refused one")
                else:  # only the standard library's attribute macros: inside an item that is rewritten they are read as built in
                    other.refused, other.nested = "inside a refused one", True

    def settle_helpers(self, ctx: CrateCtx) -> None:
        """Which attributes beside a derive are its helpers. A single name is one when no attribute macro of that name can be in
        scope: no `use` of the crate gives the name, no macro of the repo has it, no `#[macro_use] extern crate` and no glob
        import from a crate ripple has not read can bring it. Otherwise it is an attribute macro, which may rewrite the item."""
        scopes = list(self.all_scopes(ctx))
        ctx.use_names = {name for sc in scopes for name in sc.uses}
        # a `mod` written in a block is no module a module-level glob path names, so it makes no glob local
        local = {d.name for sc in scopes for defs in sc.items.values() for d in defs if d.kind in ("mod", "enum") and not d.extra.get("opaque")}
        ctx.outside_glob = any(g and g[0] not in ("crate", "self", "super", "$crate", *STD_CRATES) and g[0] not in local for sc in scopes for g in sc.globs)
        for scope, heads, file, line in ctx.pending_heads:
            if not self.heads_inert(ctx, heads):
                scope.attribute_macro = True
                ctx.taint.append(f"the attribute `#[{heads[0]}]` in {file} may be an attribute macro, which may generate impls")

    def settle_derives(self) -> None:
        """Run derive_step until it marks nothing new, flagging every scope (mark_item_macros) between runs: a scope that a derive
        marks is one where `std` may be a name a macro made, which is not the crate there, so a use there may now replace a name."""
        while True:
            for ctx in self.ctxs:
                self.mark_item_macros(ctx)
            self.cache.clear()
            if not self.derive_step():
                break

    def derive_step(self) -> bool:
        """Mark the scope of each standard derive, written by its bare name, that something may replace here: a `#[macro_use] extern
        crate`, a `#[prelude_import]` or the `#![feature(prelude_import)]` of the crate root that allows one (the crate's own prelude: the
        standard prelude is no longer std's, and the `use` that gives the name is in the prelude's module, which is on no derive's chain,
        and the attribute may be where ripple does not read: a macro body, an include!d file, a `mod` in a block) or a macro of the repo of that name (none is scoped by module:
        they replace the name at every derive of the crate), or
        a `use` that gives the name, is not proved to be the standard library's own derive, and is in a scope the derive sees.
        Returns whether it marked one it had not (scope flags must be current: mark_item_macros)."""
        changed = False
        for ctx in self.ctxs:
            pending = [i for i in range(len(ctx.pending_derives)) if i not in ctx.derive_replaced]
            if not pending:
                continue
            std_replaced = self.prelude_may_not_be_std(ctx)
            seen: dict[tuple[int, str], bool] = {}  # (scope, name) -> whether the scope holds a use of the name that is not proved std's
            for i in pending:
                scope, names, file, line = ctx.pending_derives[i]
                hit = next((n for n in names if ctx.macro_use_extern or ctx.prelude_import or std_replaced or n in self.repo_macros or self.use_replaces_derive(scope, n, seen)), None)
                if hit is not None:
                    ctx.derive_replaced[i] = hit
                    scope.attribute_macro = True
                    changed = True
        return changed

    def prelude_may_not_be_std(self, ctx: CrateCtx) -> bool:
        """Whether the standard prelude of the crate may be another crate's, which gives a bare `Debug` with no `use` of it anywhere.
        rustc injects `::std::prelude::rust_2021::*` (`::core::` in a `#![no_std]` crate) and resolves `std` in the extern prelude, which
        cargo fills from `--extern <name>`: a dependency keyed `std` or `core` (any table: the crate's targets differ in which they
        get), a dependency whose library is called so (the key is not the name: probe std_lib_named_std), and the package's own library
        for its binaries and tests (std_own_lib_named_std). A package of the repo that a dependency names, whose library ripple did not
        read, may be one of those. `extern crate x as std;` and `extern crate self as std;` are not (probes std_extern_crate_alias,
        std_self_alias), which is why this reads the manifests and not Index.externs, where those aliases are. What cannot be read: a
        library of that name in a package outside the repo (a registry crate cannot be published as `std`, and `rustc-std-workspace-*` is
        the only one with a library called so), and flags cargo is given from outside the repo."""
        package = ctx.crate.package
        if package.lib_name in ("std", "core") or any(key in package.deps for key in ("std", "core", "rustc_std_workspace_core", "rustc_std_workspace_std")):
            return True
        for target in package.deps.values():
            if target is None or target == rs_modules.AMBIGUOUS:
                continue
            lib = self.lib_ctx.get(target)
            if lib is None or lib.crate.package.lib_name in ("std", "core"):
                return True
        return False

    def use_replaces_derive(self, scope: Scope, name: str, seen: dict[tuple[int, str], bool]) -> bool:
        """Whether the bare `name` in `#[derive(name)]`, written on an item of `scope`, may be a derive a `use` gave. rustc resolves it as
        a macro path in the item's scope: that scope's own names, then each block around it up to the module's (a module's `use`s
        are its own: a parent's and a child's are not seen, and neither is another function body's or a sibling block's), then the
        macro_use, extern and standard preludes. So the `use`s that can give it are those of `scope` and the scopes around it (`outer`).
        A glob that gives another `Debug` beside the prelude's is rustc's error E0659, as is a `use` a macro expanded, so neither can be
        what a derive that builds names (the probes of bench/fixtures/rust-probes/crates/_derive_scope); a name an item declares is no
        macro's. Any one use of the name on that chain that is not proved std's marks it, whichever of them would win in rustc."""
        s: Scope | None = scope
        while s is not None:
            key = (id(s), name)
            if key not in seen:
                seen[key] = name in STD_DERIVES and not all(self.std_derive_use(s, name, segs) for segs in s.uses.get(name, ()))
            if seen[key]:
                return True
            s = s.outer
        return False

    def std_derive_use(self, scope: Scope, name: str, segs: list[str]) -> bool:
        """Whether a `use` written in `scope`, by the path `segs`, that gives the name of a standard derive gives the standard library's
        own: the first segment is the real standard crate (the lookup and the extern prelude name it as the crate, so no module, alias,
        dependency, import or block item of that name is in the way, and no macro may have made one), the path is one the library
        exports a derive under (STD_DERIVE_PATHS), and the name is not another's renamed (`use std::cmp::PartialOrd as Ord;`). A path
        ripple cannot prove is not followed: it counts as replacing the name."""
        if scope.crate.edition < 2018:  # `::std` and `std` are read from the crate root in a 2015 crate
            return False
        written = segs[1:] if segs[:1] == ["::"] else segs
        if len(written) < 3 or written[0] not in ("std", "core", "alloc"):
            return False
        crate, rest = written[0], tuple(written[1:])
        if rest[-1] != name:  # a rename: the name is another item's
            return False
        if rest not in STD_DERIVE_PATHS or (crate == "alloc" and rest != ("fmt", "Debug")):
            return False
        named = self.externs(scope.crate).get(crate)  # what the extern prelude makes of the crate's name: an `extern crate` or a dependency may name it
        if segs[0] == "::":
            return named == ("ext", "std|" + crate) or (named is None and crate != "alloc")  # alloc is in the extern prelude only by `extern crate alloc;`
        return crate != "alloc" and self.lookup(scope, crate, "type") == [("ext", "std|" + crate)]

    def heads_inert(self, ctx: CrateCtx, heads: tuple) -> bool:
        return not ctx.macro_use_extern and not ctx.outside_glob and all(h not in self.repo_macros and h not in ctx.use_names for h in heads)

    @staticmethod
    def all_scopes(ctx: CrateCtx):
        yield from ctx.scopes.values()
        yield from ctx.blocks.values()

    def is_std_macro(self, scope: Scope, name: str, qualified: bool, known: frozenset = STD_PURE_MACROS) -> bool:
        """Whether `name!` is certainly a standard macro of a list (by default, the ones that define no names)."""
        if name not in known:
            return False
        if qualified:
            return True
        if name in self.repo_macros or scope.crate.macro_use_extern:
            return False
        s = scope
        while s is not None:
            if name in s.uses or s.macro_use_mod:
                return False
            s = s.outer
        return not scope.module_scope.uses.get(name)

    def note_derives(self, d: Def, attrs: Attrs) -> None:
        self.derives[d].update(attrs.derives)
        if attrs.macro:
            self.derives[d].add("*attribute")

    def collect_use(self, scope: Scope, node, prefix: list[str], src: bytes, vis: tuple = PRIVATE, cfg: bool = False) -> None:
        if node is None:
            return
        t = node.type
        if t in ("identifier", "self", "crate", "super", "metavariable"):
            segs = prefix + [name_text(src, node)]
            name = segs[-1]
            if name == "self":
                segs = segs[:-1]
                name = segs[-1] if segs else "self"
            scope.uses[name].append(segs)
            scope.use_vis[name].append(vis)
            scope.use_cfg[name].append(cfg)
        elif t == "scoped_identifier":
            segs = prefix + path_segs(node, src)
            scope.uses[segs[-1]].append(segs)
            scope.use_vis[segs[-1]].append(vis)
            scope.use_cfg[segs[-1]].append(cfg)
        elif t == "use_as_clause":
            segs = prefix + path_segs(node.child_by_field_name("path"), src)
            alias = name_text(src, node.child_by_field_name("alias"))
            if segs and segs[-1] == "self":
                segs = segs[:-1]
            if alias == "_":
                scope.anonymous = True
            else:
                scope.uses[alias].append(segs)
                scope.use_vis[alias].append(vis)
                scope.use_cfg[alias].append(cfg)
        elif t == "use_wildcard":
            p = node.named_children[0] if node.named_children else None
            scope.globs.append(prefix + (path_segs(p, src) if p is not None else []))
            scope.glob_vis.append(vis)
            scope.glob_cfg.append(cfg)
        elif t == "scoped_use_list":
            p = node.child_by_field_name("path")
            self.collect_use(scope, node.child_by_field_name("list"), prefix + (path_segs(p, src) if p is not None else []), src, vis, cfg)
        elif t == "use_list":
            for child in node.named_children:
                self.collect_use(scope, child, prefix, src, vis, cfg)

    def collect_trait(self, d: Def, item, scope: Scope, m: Module, ctx: CrateCtx, owner_id: str | None, rw: bool = False) -> None:
        src = m.src
        decls: dict[str, Def] = {}
        body = item.child_by_field_name("body")
        generics = generic_names(item, src)
        for f in body.named_children if body is not None else ():
            if f.type in ("function_item", "function_signature_item"):
                fname = name_text(src, f.child_by_field_name("name"))
                key = (m.file, f.start_byte)
                sid = self.make_id(m, owner_id, d.name, fname)
                fattrs = attrs_of(f, src, ctx.tools)
                made = Def("decl", fname, key, f, scope, {"trait": d, "has_body": f.type == "function_item", "cfg": fattrs.cfg, "rw": rw or fattrs.macro})
                if fname in decls:
                    decls[fname].extra["twin"] = True  # the same declaration under different cfgs: which is built is not known
                    made.extra["twin"] = True
                decls[fname] = made
                self.claim(key, sid, "method", m.file, f)
                fn_body = f.child_by_field_name("body")
                if fn_body is not None:
                    self.bodies.append(BodySpec(key, f, scope, m, src, None, d, (*generics, *generic_names(f, src)), rw or fattrs.macro))
                    self.collect_blocks(fn_body, scope, m, ctx, sid, rw or fattrs.macro)
            elif f.type in ("const_item", "macro_invocation"):
                self.bodies.append(BodySpec((m.file, -1), f, scope, m, src, None, d, generics, rw))
                if f.type == "macro_invocation":
                    d.extra["macro_body"] = True
                    self.note_macro(scope, f, src, ctx, m.file, names=False)
        self.trait_decl[d] = decls
        self.trait_fn_names |= set(decls)
        self.supertraits[d] = (item.child_by_field_name("bounds"), scope)

    def collect_impl(self, scope: Scope, item, m: Module, owner_id: str | None, attrs: Attrs | None = None, rw: bool = False) -> None:
        src = m.src
        spec = ImplSpec(scope, m, item, src, generic_names(item, src), owner_id)
        spec.cfg = bool(attrs and attrs.cfg)
        spec.rewritten = rw
        tnode, trnode = item.child_by_field_name("type"), item.child_by_field_name("trait")
        if tnode is not None:
            spec.tname = self.type_name(tnode, src)
        if trnode is not None:
            spec.trait_plain = path_segs(trnode, src)[-1]
            spec.trait_text = text(src, trnode)
            self.trait_counts[(m.file, m.inline, owner_id, spec.tname, spec.trait_plain)] += 1
        self.impl_specs.append(spec)
        body = item.child_by_field_name("body")
        for f in body.named_children if body is not None else ():
            if f.type in ("const_item", "macro_invocation"):
                self.bodies.append(BodySpec((m.file, -1), f, scope, m, src, spec, None, spec.generics, rw))
            if f.type == "macro_invocation":
                mnode = f.child_by_field_name("macro")
                spec.macro_body = path_segs(mnode, src)[-1] if mnode is not None else "?"
                self.note_macro(scope, f, src, scope.crate, m.file, names=False)

    def collect_blocks(self, node, scope: Scope, m: Module, ctx: CrateCtx, owner_id: str, rw: bool = False) -> None:
        """Walk a function body for blocks that declare items (nested fns, uses, impls, structs). Each gets a
        scope whose outer scope is the one around it. A nested function is a symbol of its own."""
        stack: list[tuple[object, Scope]] = [(node, scope)]
        while stack:
            n, current = stack.pop()
            if n.type == "block":
                if any(c.type in ITEM_NODES or statement_macro(c) is not None or c.type in ("let_declaration", "expression_statement") and attrs_of(c, m.src, ctx.tools).generates for c in n.named_children):
                    inner = Scope(ctx, None, outer=current, home=current.module_scope, rewritten=rw or current.rewritten)
                    ctx.blocks[(m.file, n.start_byte)] = inner
                    self.collect_items(inner, n, m, ctx, owner_id, block=True, rw=rw)
                    current = inner
            elif n.type in NESTED_ITEMS or n.type == "macro_invocation":
                continue
            stack.extend((child, current) for child in n.named_children)

    # ---- impls
    @staticmethod
    def type_name(tnode, src: bytes) -> str:
        """The name an impl's type goes by in ids: the last segment of its path, references and generic arguments
        dropped (`impl<T> Foo<T>`, `impl Foo<u8>` and `impl &Foo` are all Foo); a type with no path is as written."""
        while tnode.type in ("reference_type", "pointer_type") and tnode.child_by_field_name("type") is not None:
            tnode = tnode.child_by_field_name("type")
        if tnode.type == "generic_type":
            tnode = tnode.child_by_field_name("type")
        if tnode.type in ("type_identifier", "scoped_type_identifier", "identifier"):
            return path_segs(tnode, src)[-1]
        return squash(text(src, tnode))

    def resolve_impl(self, spec: ImplSpec) -> None:
        tnode, trnode = spec.node.child_by_field_name("type"), spec.node.child_by_field_name("trait")
        spec.self_target = self.type_target(spec.scope, tnode, spec.src, spec.generics) if tnode is not None else None
        spec.trait_target = self.type_target(spec.scope, trnode, spec.src, spec.generics) if trnode is not None else None
        spec.via_alias = tnode is not None and self.through_alias(spec.scope, tnode, spec.src)
        # a header naming a path ripple could not resolve may be an impl for (or of) anything: the tables miss it
        lost = (tnode is not None and spec.self_target is None and tnode.type not in NOT_A_PATH_TYPE) or (trnode is not None and spec.trait_target is None)
        if lost:
            shown = squash(text(spec.src, spec.node.child_by_field_name("trait"))) + " for " if trnode is not None else ""
            shown += squash(text(spec.src, tnode)) if tnode is not None else ""
            spec.lost = f"the impl `impl {shown}` in {spec.module.file} names a type or trait ripple could not resolve, so it may be an impl for any type"

    def through_alias(self, scope: Scope, tnode, src: bytes) -> bool:
        """Whether a written type names a type alias (so the arguments it is written with are not the type's)."""
        while tnode.type == "generic_type":
            tnode = tnode.child_by_field_name("type")
        if tnode.type not in ("type_identifier", "scoped_type_identifier", "identifier"):
            return False
        return any(r[0] == "def" and r[1].kind == "type" for r in self.resolve_path(scope, path_segs(tnode, src), "type"))

    def register_impl(self, spec: ImplSpec) -> None:
        """The impl's functions as symbols (ids by the type, and by the trait for a trait impl), and the impl in the
        type and trait indexes."""
        m, src, node = spec.module, spec.src, spec.node
        trnode = node.child_by_field_name("trait")
        # the trait as written: its last segment, with generic arguments when a type implements it more than once in a place
        again = trnode is not None and self.trait_counts[(m.file, m.inline, spec.owner_id, spec.tname, spec.trait_plain)] > 1
        body = node.child_by_field_name("body")
        for f in body.named_children if body is not None else ():
            if f.type == "const_item" and f.child_by_field_name("name") is not None:
                cattrs = attrs_of(f, src, spec.scope.crate.tools)
                cname = name_text(src, f.child_by_field_name("name"))
                spec.consts.setdefault(cname, []).append(
                    Def("assoc_const", cname, (m.file, f.start_byte), f, spec.scope, {"impl": spec, "vis": visibility(f, src), "cfg": spec.cfg or cattrs.cfg, "rw": spec.rewritten or cattrs.macro})
                )
            if f.type != "function_item":
                continue
            fattrs = attrs_of(f, src, spec.scope.crate.tools)
            fname = name_text(src, f.child_by_field_name("name"))
            key = (m.file, f.start_byte)
            if trnode is None:
                sid = self.make_id(m, spec.owner_id, spec.tname, fname)
            else:
                trait = spec.trait_plain
                if again and trnode.type == "generic_type":
                    args = trnode.child_by_field_name("type_arguments")
                    trait += squash(text(src, args)) if args is not None else ""
                sid = self.make_id(m, spec.owner_id, f"<{spec.tname} as {trait}>", fname)
            rw = spec.rewritten or fattrs.macro
            d = Def("method", fname, key, f, spec.scope, {"impl": spec, "vis": visibility(f, src), "cfg": spec.cfg or fattrs.cfg, "rw": rw})
            spec.methods.setdefault(fname, []).append(d)
            self.claim(key, sid, "method", m.file, f)
            self.bodies.append(BodySpec(key, f, spec.scope, m, src, spec, None, (*spec.generics, *generic_names(f, src)), rw))
            fn_body = f.child_by_field_name("body")
            if fn_body is not None:
                self.collect_blocks(fn_body, spec.scope, m, spec.scope.crate, sid, rw)
        target = spec.self_target
        if trnode is None:
            if target is not None and target[0] == "def":
                for name, defs in (*spec.methods.items(), *spec.consts.items()):
                    self.inherent[target[1]][name].extend(defs)
        else:
            if target is not None and target[0] == "def":
                self.trait_impls[target[1]].append(spec)
            if spec.trait_target is not None and spec.trait_target[0] == "def":
                self.impls_of[spec.trait_target[1]].append(spec)

    # ---- name resolution
    def externs(self, ctx: CrateCtx) -> dict[str, tuple]:
        """What a crate can name without importing it: the dependencies of its package under the names they are
        written as, and the package's own library (for its binaries, tests, examples and benches)."""
        if ctx.externs is not None:
            return ctx.externs
        package, out = ctx.crate.package, {}
        for name, target in package.deps.items():
            if target is None:
                out[name] = ("ext", "crate:" + name)
            elif target == rs_modules.AMBIGUOUS:
                out[name] = ("unknown", DEP_AMBIGUOUS)
            elif target in self.lib_ctx:
                out[name] = ("crate", self.lib_ctx[target])
            else:
                out[name] = ("unknown", DEP_NOT_READ)
        if package.lib_name and ctx.crate.kind not in ("lib", "build") and package.dir in self.lib_ctx:
            out[package.lib_name] = ("crate", self.lib_ctx[package.dir])
        for names in (ctx.root.items.values() if ctx.root is not None else ()):  # `extern crate x as y;` in the root names y everywhere
            for d in names:
                if d.kind == "extern_crate":
                    alias = next(n for n, defs in ctx.root.items.items() if d in defs)
                    target = ("crate", ctx) if d.name == "self" else out.get(d.name.replace("-", "_"))
                    if target is None and d.name in STD_CRATES:
                        target = ("ext", "std|" + d.name)
                    out[alias] = target if target is not None else ("ext", "crate:" + d.name)
        ctx.externs = out
        return out

    def lookup(self, s: Scope, name: str, ns: str, guard: tuple = (), first: bool = True, viewer: Scope | None = None) -> list[tuple]:
        """What a name is in a scope: ("def", Def), ("crate", CrateCtx), ("ext", why) or ("unknown", why). `first` is
        a path's first segment (which may be a crate, a prelude name or a primitive), not a member of a module. `viewer`
        is the scope whose glob import is looking in this one: it brings in only the names visible to it."""
        key = (id(s), name, ns, first, id(viewer))
        if key in guard:
            self.cuts += 1
            return []
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        before = self.cuts
        out = self._lookup(s, name, ns, (*guard, key), first, viewer)
        if self.cuts == before:  # a result cut short by a cycle is not kept
            self.cache[key] = out
        return out

    @staticmethod
    def within(scope: Scope, ancestor: Scope) -> bool:
        """Whether a module scope is `ancestor` or inside it."""
        here = scope.module_scope
        while here is not None:
            if here is ancestor:
                return True
            here = here.parent
        return False

    def module_named(self, home: Scope, segs: tuple) -> Scope | None:
        """The module a `pub(in path)` names, from the module the item is in; None when ripple cannot find it."""
        cur: Scope | None = None
        for i, seg in enumerate(segs):
            if i == 0 and seg in ("crate", "self", "super"):
                cur = {"crate": home.crate.root, "self": home, "super": home.parent}[seg]
            elif seg == "super":
                cur = cur.parent if cur is not None else None
            else:
                mods = [d for d in cur.items.get(seg, ()) if d.kind == "mod"] if cur is not None else []
                cur = mods[0].extra["scope"] if len(mods) == 1 else None
            if cur is None:
                return None
        return cur

    def sees(self, vis: tuple | None, home: Scope, viewer: Scope | None) -> bool | None:
        """Whether something with visibility `vis`, declared in `home`, can be named from `viewer` (None: a
        `pub(in path)` names a module ripple cannot find). Without a viewer, or without a visibility, it can."""
        if viewer is None or vis is None or vis == PUBLIC:
            return True
        home = home.module_scope
        if home.crate is not viewer.crate:
            return False
        if vis == ("crate",):
            return True
        if vis == PRIVATE:
            return self.within(viewer, home)
        if vis == ("super",):
            return home.parent is not None and self.within(viewer, home.parent)
        target = self.module_named(home, vis[1]) if vis[0] == "in" else None
        return None if target is None else self.within(viewer, target)

    def _lookup(self, s: Scope, name: str, ns: str, guard: tuple, first: bool, viewer: Scope | None) -> list[tuple]:
        if s.item_macros and s.outer is not None and ns == "value":
            return [("unknown", MACRO_MAY_MAKE)]  # a block that invokes a macro: it may bind a local of any name, which shadows every item
        found: list[tuple] = []
        hidden = False  # something of the name whose visibility ripple cannot decide
        old = s.crate.edition < 2018  # a 2015 crate reads `use` and extern crate by rules ripple does not read
        plain: set = set()  # the results something unconditional brings in
        for d in s.items.get(name, ()):
            if in_namespace(d, ns):
                seen = self.sees(d.extra.get("vis"), s, viewer)
                if seen:
                    rewritten = d.extra.get("rw") or d.extra.get("heads") and not self.heads_inert(s.crate, d.extra["heads"])
                    result = ("unknown", BLOCK_MOD) if d.extra.get("opaque") else ("unknown", ATTR_MACRO) if rewritten else ("def", d)
                    found.append(result)
                    if not d.extra.get("cfg"):
                        plain.add(_key(result))
                hidden |= seen is None
        for number, segs in enumerate(s.uses.get(name, ())):
            seen = self.sees(s.use_vis[name][number], s, viewer)
            if seen:
                got = [("unknown", EDITION)] if old else self.resolve_path(s, segs, ns, guard, is_use=True)
                found += got
                if not s.use_cfg[name][number]:
                    plain.update(_key(r) for r in got)
            hidden |= seen is None
        found = _unique(found)
        real = [f for f in found if f[0] != "unknown"]
        if real:
            if all(_key(r) not in plain or (r[0] == "def" and r[1].extra.get("cfg")) for r in real):
                # everything found is under a #[cfg]: with it off, what is found is whatever the rest of the lookup brings
                rest = self._fallback(s, name, ns, guard, first, viewer, [])
                if rest not in ([], [("unknown", NOT_FOUND)]):
                    return [("unknown", CFG_UNKNOWN)]
            return real
        if found:
            return found
        if hidden:
            return [("unknown", INVISIBLE_PATH)]
        return self._fallback(s, name, ns, guard, first, viewer, found)

    def _fallback(self, s: Scope, name: str, ns: str, guard: tuple, first: bool, viewer: Scope | None, found: list[tuple]) -> list[tuple]:
        """What a name is when the scope declares and imports nothing of it: a macro's, a glob's, the enclosing scope's, a
        crate's, the prelude's."""
        if s.item_macros:  # a macro may have made the name, and what it makes beats a glob, an outer scope and the prelude
            return [("unknown", MACRO_MAY_MAKE)]
        old = s.crate.edition < 2018
        outside = unreadable = False  # a glob from outside the repo, or one ripple cannot follow, may bring the name
        uncertain: list[str] = []  # the globs of modules in which a macro may have made the name
        if old and s.globs:
            unreadable = True
            uncertain.append(EDITION)
        conditional = False  # a name found through a glob, a use or an item under a #[cfg]
        for number, segs in enumerate(s.globs):
            if old:
                break
            glob = ("glob", id(s), number)
            if glob in guard:  # the path of a glob is not looked up through that glob itself (`use Enum::*;`)
                self.cuts += 1
                continue
            seen = self.sees(s.glob_vis[number], s, viewer)
            if not seen:
                unreadable |= seen is None
                continue
            for kind, x in self.resolve_path(s, segs, "type", (*guard, glob), is_use=True):
                if kind == "def" and x.kind == "mod":
                    got = self.lookup(x.extra["scope"], name, ns, guard, first=False, viewer=viewer or s)
                elif kind == "def" and x.kind == "enum":
                    got = [("def", x.extra["variants"][name])] if ns == "value" and name in x.extra["variants"] else []
                elif kind == "crate" and x is not None:
                    got = self.lookup(x.root, name, ns, guard, first=False, viewer=viewer or s)
                elif kind in ("ext", "crate"):
                    outside = True
                    continue
                else:
                    unreadable = True
                    if (kind, x) == ("unknown", BLOCK_MOD):
                        uncertain.append(BLOCK_MOD)  # the glob is of a `mod` written in a block: say so, not that it is from outside the repo
                    continue
                found += [g for g in got if g[0] != "unknown"]
                uncertain += [g[1] for g in got if g[0] == "unknown"]
                conditional |= bool(got) and (s.glob_cfg[number] or any(g[0] == "def" and g[1].extra.get("cfg") for g in got))
        found = _unique(found)
        if found:
            return [("unknown", CFG_UNKNOWN)] if conditional else found
        unreadable |= bool(uncertain)
        maybe = outside or unreadable
        why = uncertain[0] if uncertain else BLOCK_GLOB
        if s.outer is not None:  # a block: look in the scope around it
            around = self.lookup(s.outer, name, ns, guard, first)
            if not maybe:
                return around
            real = [f for f in around if f[0] != "unknown"]
            if real and all(f[0] == "ext" for f in real):
                return real
            if not real and outside and not unreadable:
                return [("ext", "external-glob")]
            return [("unknown", why)]
        if first:
            extern = self.externs(s.crate)
            if maybe:
                if ns == "type" and name in extern and extern[name][0] != "ext":
                    return [("unknown", why)]
                return [("ext", "external-glob")] if outside and not unreadable else [("unknown", why)]
            if old:
                return [("unknown", EDITION)]
            if ns == "type" and name in extern:
                return [extern[name]]
            if ns == "type" and name in STD_CRATES:
                return [("ext", "std|" + name)]
            if ns == "type" and name in PRELUDE_TYPES:
                return [("ext", "prelude|" + name)]
            if ns == "value" and name in PRELUDE_VALUES:
                return [("ext", "prelude-ctor|" + name if name in CONSTRUCTOR_VALUES else "prelude|" + name)]
            if ns == "type" and name in PRIMITIVES:
                return [("ext", "primitive|" + name)]
        if maybe:
            return [("unknown", why)]
        return [("unknown", NOT_FOUND)] if first else []

    def resolve_path(self, s: Scope, segs: list[str], ns: str, guard: tuple = (), is_use: bool = False) -> list[tuple]:
        """What a path written in a scope is. A use path is every namespace its last segment is in."""
        if not segs:
            home = s.module_scope
            return [("def", home.mod_def or self.root_def(home.crate))]
        cur: list[tuple]
        i = 0
        first = segs[0]
        ctx = s.crate
        if first == "::":  # a global path: its first segment is a crate
            if len(segs) < 2:
                return [("unknown", NOT_FOUND)]
            name = segs[1]
            extern = self.externs(ctx)
            cur = [extern[name]] if name in extern else [("ext", "std|" + name)] if name in STD_CRATES else [("unknown", NOT_FOUND)]
            i = 2
        elif first in ("crate", "$crate"):
            cur = [("def", self.root_def(ctx))]
            i = 1
        elif first in ("self", "super"):
            sc = s.module_scope
            while i < len(segs) and segs[i] in ("self", "super"):
                if segs[i] == "super":
                    sc = sc.parent
                    if sc is None:
                        return [("unknown", BAD_SUPER)]
                i += 1
            cur = [("def", sc.mod_def or self.root_def(ctx))]
        else:
            last = len(segs) == 1
            if last and is_use:
                cur = [r for ns2 in ("type", "value", "macro") for r in self.lookup(s, first, ns2, guard)]
                real = [c for c in _unique(cur) if c[0] != "unknown"]
                return real or _unique(cur)[:1]
            cur = self.lookup(s, first, ns if last else "type", guard)
            i = 1
        while i < len(segs):
            seg = segs[i]
            last = i == len(segs) - 1
            want = ns if last else "type"
            nxt: list[tuple] = []
            for kind, x in cur:
                if kind in ("ext", "unknown"):
                    nxt.append((kind, ext_step(x, seg) if kind == "ext" else x))
                elif kind == "crate":
                    nxt += self.member(x.root, seg, want, guard, is_use and last) if x is not None else [("ext", "crate")]
                elif kind == "def":
                    if x.kind == "mod":
                        nxt += self.member(x.extra["scope"], seg, want, guard, is_use and last)
                    elif x.kind == "extern_crate":
                        nxt += self.named_crate(x.scope.crate, x.name, seg, want, guard, is_use and last)
                    elif x.kind == "enum" and seg in x.extra["variants"]:
                        nxt.append(("def", x.extra["variants"][seg]))
                    elif x.kind in ("struct", "enum", "union", "trait", "type"):
                        nxt.append(("assoc", (x, seg)))
                    else:
                        nxt.append(("unknown", PATH_NAMES.format(f"a {x.kind}") + " in the middle of a path"))
                else:
                    nxt.append(("unknown", "the path goes through an associated item"))
            cur = _unique(nxt)
            i += 1
            if not cur:
                break
        return cur

    def named_crate(self, ctx: CrateCtx, crate: str, seg: str, ns: str, guard: tuple, any_ns: bool) -> list[tuple]:
        """`seg` inside the crate an `extern crate` item names: a crate of the repo, or one outside it."""
        if crate == "self":  # `extern crate self as name;`: the crate itself
            return self.member(ctx.root, seg, ns, guard, any_ns)
        name = crate.replace("-", "_")
        target = self.externs(ctx).get(name)
        if target is not None and target[0] == "crate":
            return self.member(target[1].root, seg, ns, guard, any_ns)
        if target is None and name in STD_CRATES:
            return [("ext", f"std|{name}::{seg}")]
        return [target if target is not None else ("ext", "crate:" + name)] if target is None or target[0] != "crate" else []

    def member(self, scope: Scope, seg: str, ns: str, guard: tuple, any_ns: bool) -> list[tuple]:
        if any_ns:
            out = [r for ns2 in ("type", "value", "macro") for r in self.lookup(scope, seg, ns2, guard, first=False)]
        else:
            out = self.lookup(scope, seg, ns, guard, first=False)
        if not out:
            return [("unknown", MACRO_MAY_MAKE if scope.may_make else NOT_IN_MODULE)]
        return out

    def root_def(self, ctx: CrateCtx) -> Def:
        return ctx.root_def

    def type_target(self, scope: Scope, tnode, src: bytes, generics: tuple = (), depth: int = 0) -> tuple | None:
        """What a written type is: ("def", Def), ("ext", why), ("generic", name), or None when it is not a type of
        one name (a trait object, a tuple, a path that does not resolve to one thing)."""
        if tnode is None or depth > 8:
            return None
        t = tnode.type
        if t == "generic_type":
            return self.type_target(scope, tnode.child_by_field_name("type"), src, generics, depth)
        if t in ("reference_type", "pointer_type"):
            return None  # `&S` is not `S`: an impl for it, or a path through it, is not one of S
        if t == "primitive_type":
            return ("ext", "primitive")
        if t in ("array_type", "slice_type", "tuple_type"):
            return ("ext", "builtin type")
        if t in ("type_identifier", "scoped_type_identifier", "identifier"):
            segs = path_segs(tnode, src)
            if len(segs) == 1 and segs[0] in generics:
                return ("generic", segs[0])
            found = [r for r in self.resolve_path(scope, segs, "type") if r[0] in ("def", "ext", "crate")]
            if len(found) != 1:
                return None
            kind, x = found[0]
            if kind == "def" and x.kind == "type":  # an alias: the type it names
                inner = x.node.child_by_field_name("type")
                if inner is None or x.node.child_by_field_name("type_parameters") is not None and inner.type != "generic_type":
                    return None
                return self.type_target(x.scope, inner, x.scope.module_scope.module.src, (), depth + 1)
            if kind == "def" and x.kind not in ("struct", "enum", "union", "trait"):
                return None
            return found[0] if kind != "crate" else None
        return None

    # ---- what a call is
    def classify(self, body: Body, call) -> tuple[tuple, str, int]:
        """(what the call is, its callee as written, where the callee's last name ends) for a call_expression."""
        result, written_as = self.classify_callee(body, call)
        fn = call.child_by_field_name("function")
        if fn is not None and fn.type == "generic_function":
            fn = fn.child_by_field_name("function")
        return result, written_as, (fn.end_byte if fn is not None else call.end_byte)

    def classify_callee(self, body: Body, call) -> tuple[tuple, str]:
        src = body.src
        fn = call.child_by_field_name("function")
        if fn is None:
            return unresolved(NO_CALLEE), ""
        if fn.type == "generic_function":
            fn = fn.child_by_field_name("function")
        t = fn.type
        if t == "field_expression":
            recv, name = fn.child_by_field_name("value"), text(src, fn.child_by_field_name("field"))
            return unresolved(METHOD), f"{short(src, recv)[:60].replace(' .', '.')}.{name}"
        if t == "identifier":
            return self.path_call(body, [name_text(src, fn)]), text(src, fn)
        if t == "scoped_identifier":
            path = fn.child_by_field_name("path")
            if path is not None and path.type == "bracketed_type":
                return self.qualified(body, path, name_text(src, fn.child_by_field_name("name"))), short(src, fn)
            segs = path_segs(fn, src)
            return self.path_call(body, segs, written_args(path, src)), written(segs)
        if t == "parenthesized_expression":
            return unresolved(CALLS_VALUE), short(src, fn)
        return unresolved(CALLS_VALUE), short(src, fn)

    def path_call(self, body: Body, segs: list[str], targs: tuple | None = None) -> tuple:
        if body.ctx.edition < 2018:
            return unresolved(EDITION)
        first = segs[0]
        if len(segs) == 1 and first in body.locals:
            return unresolved(LOCAL)
        if len(segs) >= 2 and first in body.generics:
            return unresolved(GENERIC_PATH)
        if first == "Self":
            return self.self_path(body, segs)
        found = self.resolve_path(body.scope, segs, "value")
        found = [r for r in found if r[0] != "unknown"] or found
        if not found:
            return unresolved(NOT_FOUND)
        if len(found) > 1:
            if all(r[0] == "ext" for r in found):
                found = found[:1]
            elif all(r[0] == "unknown" for r in found) and MACRO_MAY_MAKE in (r[1] for r in found):
                return unresolved(MACRO_MAY_MAKE)  # nothing is named twice: two reasons it is not known, and a macro that may have made it is one
            else:
                return unresolved(SEVERAL)
        kind, x = found[0]
        if kind == "unknown":
            return unresolved(x)
        if kind == "ext":
            return self.outside(body, segs, x)
        if kind == "assoc":
            return self.assoc(body, x[0], x[1], targs)
        if kind == "crate":
            return unresolved(CRATE_PATH_ERR)
        if x.kind == "fn":
            return edge(x)
        if x.kind == "foreign":
            return external(FOREIGN)
        if x.kind in ("tuple_struct", "unit_struct", "variant"):
            return CTOR
        if x.kind in ("const", "static"):
            return unresolved(CALLS_CONST)
        return unresolved(PATH_NAMES.format(f"a {x.kind}"))

    def outside(self, body: Body, segs: list[str], why: str) -> tuple:
        """A call whose path ends outside the repo. It is outside only if nothing of the repo can be what runs. A repo
        trait that declares a function of the name may be the one called (an extension trait of a type from outside),
        and so may an impl in the repo of a trait from outside (`fmt::Display::fmt(&self.0, f)` runs the repo's
        `Display for Inner`, which Self, taken from the argument, selects), unless the path names a type from the
        standard library, which Self then is. `why` is what kind of outside it is, and for the standard library the
        path the name resolved to (so a `use std::fmt::Display as String` is not the type String)."""
        base, _, path = why.partition("|")
        parts = path.split("::")
        if base == "prelude-ctor":
            return CTOR
        if len(segs) >= 2:
            name = segs[-1]
            if name in self.trait_fn_names or name in self.foreign_impl_fns:
                return unresolved(EXT_TRAIT_SHADOW)
            concrete = base in ("prelude", "primitive", "std") and len(parts) >= 2 and parts[-2] in STD_TYPES  # by what the path resolved to, not how it is written
            if name in self.impl_fn_names and not concrete:
                return unresolved(EXT_TRAIT_REACH)
            if parts[0] in PRELUDE_TRAITS and base == "prelude":
                return external(EXT_TRAIT)
        if base == "primitive":
            return external(PRIMITIVE)
        if base == "external-glob":
            return external(EXT_GLOB)
        if base == "std":
            return external(STD_PATH)
        if base == "prelude":
            return external(PRELUDE)
        return external(CRATE_PATH)

    def self_path(self, body: Body, segs: list[str]) -> tuple:
        if len(segs) != 2:
            return unresolved(SELF_GENERIC)
        name = segs[1]
        if body.impl is None:
            if body.trait is None:
                return unresolved(SELF_OUTSIDE)
            return self.trait_call(body.trait, name)  # Self is whatever implements the trait
        target = body.impl.self_target
        if target is None or target[0] != "def":
            return unresolved(SELF_GENERIC)
        spec = body.impl
        args = None if spec.via_alias else written_args(spec.node.child_by_field_name("type"), spec.src)
        return self.assoc(body, target[1], name, args)

    def with_supertraits(self, traits: list[Def]) -> list[Def]:
        out: list[Def] = []
        stack = list(traits)
        while stack:
            tr = stack.pop(0)
            if tr in out:
                continue
            out.append(tr)
            bounds, scope = self.supertraits.get(tr, (None, None))
            for b in bounds.named_children if bounds is not None else ():
                target = self.type_target(scope, b, scope.module_scope.module.src)
                if target is not None and target[0] == "def" and target[1].kind == "trait":
                    stack.append(target[1])
        return out

    def trait_call(self, trait: Def, name: str) -> tuple:
        """`Trait::f(..)`: the trait's declaration, and every repo impl of it that defines f as an override (an
        impl outside the repo is not seen, as a subclass outside the repo is not)."""
        hits = [self.trait_decl[t][name] for t in self.with_supertraits([trait]) if name in self.trait_decl.get(t, {})]
        if not hits:
            return unresolved(TRAIT_NO_DECL)
        if len(hits) > 1 or hits[0].extra.get("twin"):
            return unresolved(SEVERAL_BOUNDS if len(hits) > 1 else SEVERAL)
        decl = hits[0]
        if decl.extra.get("rw"):
            return unresolved(ATTR_MACRO)
        overrides = [d for spec in self.impls_of.get(decl.extra["trait"], []) for d in spec.methods.get(name, ())]
        return edge(decl, overrides)

    def trait_visible(self, body: Body, spec: ImplSpec) -> bool:
        """Whether the trait of an impl is certainly in scope where the call is: the trait of the impl or trait
        being written (rustc puts it in scope), a repo trait named by an import or declaration in the scopes around the
        call. A trait from outside the repo counts as in scope: the program compiles, so whatever the call names is in
        scope, and the repo has no other trait that gives the type the name."""
        target = spec.trait_target
        if target is None:
            return False
        if target[0] == "def":
            td = target[1]
            if body.trait is td or (body.impl is not None and body.impl.trait_target == target):
                return True
            return ("def", td) in self.lookup(body.scope, td.name, "type")
        return True  # a trait from outside the repo: a call that names its function compiles only with it in scope

    # ---- which impls a path through a type can reach
    def reach(self, ctx: CrateCtx) -> list[CrateCtx]:
        """The crates a crate can name, itself first: its dependencies, and theirs."""
        cached = self.reach_cache.get(id(ctx))
        if cached is None:
            cached, todo = [ctx], [ctx]
            while todo:
                for kind, other in self.externs(todo.pop()).values():
                    if kind == "crate" and other is not None and other not in cached:
                        cached.append(other)
                        todo.append(other)
            self.reach_cache[id(ctx)] = cached
        return cached

    def impl_taint(self, call: CrateCtx, *owners: CrateCtx) -> str | None:
        """Why the impls ripple read may not be all of those a path in `call` can reach about a type or trait of
        `owners`, or None when every impl is in view: a macro (an item macro, an impl or trait body macro, an attribute
        macro or a derive that is not a standard one) or an impl header ripple could not resolve may add one, in any
        crate that can name the type or the trait (the owners' crates and the crates that use them), or a blanket impl of a
        trait the call's crate can name (its own crate and its dependencies)."""
        crates = {id(c): c for c in self.reach(call)}
        for owner in owners:
            for c in self.ctxs:
                if owner in self.reach(c):
                    crates[id(c)] = c
        for c in sorted(crates.values(), key=lambda c: c.index):
            if c.taint:
                return c.taint[0]
        return None

    def covers(self, spec: ImplSpec, tdef: Def) -> bool:
        """Whether an impl is for every instantiation of the type: it names the bare type, or the type with exactly its own
        distinct generic parameters (`impl<T> G<T>`)."""
        if spec.via_alias:
            return False
        args = written_args_of(spec.node.child_by_field_name("type"), spec.src)
        if args is None:
            return False
        params = generic_names(tdef.node, tdef.scope.module_scope.module.src)
        if not params:
            return not args
        return len(args) == len(params) and len(set(args)) == len(args) and all(a in spec.generics for a in args)

    def relation(self, spec: ImplSpec, tdef: Def, targs: tuple | None) -> str:
        """How an impl's type arguments stand to the ones a call is written with: `covers` (the impl is for every
        instantiation), `same` (both name the same primitive arguments), `disjoint` (different primitive arguments),
        or `unknown`, where ripple cannot tell whether the impl applies."""
        if self.covers(spec, tdef):
            return "covers"
        mine = None if spec.via_alias else written_args_of(spec.node.child_by_field_name("type"), spec.src)
        if mine is None or targs is None or len(mine) != len(targs) or not (concrete_args(mine) and concrete_args(targs)):
            return "unknown"
        return "same" if mine == targs else "disjoint"

    def assoc(self, body: Body, tdef: Def, name: str, targs: tuple | None = None, depth: int = 0) -> tuple:
        """`Type::name(..)`: an inherent function of the type wins over a trait function. A trait function is the target
        only when exactly one impl of the type gives the name, its trait is certainly in scope, and nothing else could.
        `targs` are the type arguments the call writes (None when it writes none)."""
        if tdef.kind == "trait":
            return self.trait_call(tdef, name)
        if tdef.kind == "type":
            inner = tdef.node.child_by_field_name("type")
            target = self.type_target(tdef.scope, inner, tdef.scope.module_scope.module.src) if inner is not None and depth < 4 else None
            if target is None:
                return unresolved(ALIAS_UNKNOWN)
            if target[0] == "ext":
                # a repo trait may give the outside type this name, as it may when the type is written as itself
                return unresolved(EXT_TRAIT_SHADOW) if name in self.trait_fn_names or name in self.foreign_impl_fns else external(CRATE_PATH)
            if target[0] != "def":
                return unresolved(ALIAS_UNKNOWN)
            args = written_args(inner, tdef.scope.module_scope.module.src) if tdef.node.child_by_field_name("type_parameters") is None else None
            return self.assoc(body, target[1], name, args, depth + 1)
        if tdef.kind == "enum" and name in tdef.extra["variants"]:
            return CTOR
        hit = self.inherent_hit(body, tdef, name, targs)
        if hit is not None:
            return hit
        return self.assoc_traits(body, tdef, name, targs)

    def inherent_hit(self, body: Body, tdef: Def, name: str, targs: tuple | None) -> tuple | None:
        """The result of `Type::name` when an inherent item of the type, which wins over every trait's, is what it
        reaches or may reach (None when no inherent impl can be). Such an item is the target only when its impl is for the
        instantiation the call names, it is visible here, and it is a function that exists."""
        applicable: list[Def] = []
        unsure = False
        for d in self.inherent.get(tdef, {}).get(name, []):
            relation = self.relation(d.extra["impl"], tdef, targs)
            if relation in ("covers", "same"):
                applicable.append(d)
            unsure |= relation == "unknown"
        if len(applicable) > 1:
            return unresolved(SEVERAL_INHERENT)
        if not applicable:
            return unresolved(SEVERAL_INHERENT if len(self.inherent.get(tdef, {}).get(name, [])) > 1 else INSTANTIATION) if unsure else None
        d = applicable[0]
        if d.extra.get("rw"):
            return unresolved(ATTR_MACRO)
        seen = self.sees(d.extra.get("vis"), d.extra["impl"].scope, body.scope)
        if not seen:
            return unresolved(INHERENT_NOT_VISIBLE if seen is not None else INVISIBLE_PATH)
        if d.kind != "method":
            return unresolved(CALLS_CONST)
        if d.extra.get("cfg"):  # it may not exist: it is the target only if nothing else could be
            return edge(d) if self.assoc_traits(body, tdef, name, targs) == unresolved(NO_IMPL) else unresolved(CFG_UNKNOWN)
        return edge(d)

    def assoc_traits(self, body: Body, tdef: Def, name: str, targs: tuple | None) -> tuple:
        taint = self.impl_taint(body.ctx, tdef.scope.crate)
        if taint:  # an inherent item, an override or another impl the tables do not show may be what runs
            return unresolved(IMPLS_INCOMPLETE.format(taint))
        candidates: list[tuple[Def, ImplSpec]] = []
        unsure = may_default = False
        for spec in self.trait_impls.get(tdef, []):
            relation = self.relation(spec, tdef, targs)
            decl = self.trait_decl.get(spec.trait_target[1], {}).get(name) if spec.trait_target is not None and spec.trait_target[0] == "def" else None
            gives = name in spec.methods or name in spec.consts or decl is not None and decl.extra["has_body"]
            if relation == "disjoint" or not gives:
                continue
            if relation == "unknown":
                unsure = True
                continue
            if name in spec.consts:
                return unresolved(CALLS_CONST)
            if name in spec.methods:
                candidates += [(d, spec) for d in spec.methods[name]]
                # under a #[cfg] that is off, the trait's own default is what runs
                may_default |= decl is not None and decl.extra["has_body"] and any(d.extra.get("cfg") for d in spec.methods[name])
            else:
                candidates.append((decl, spec))
        derived = any(name in DERIVE_FNS.get(d, ()) for d in self.derives.get(tdef, ()))
        if unsure:
            return unresolved(INSTANTIATION)
        if may_default:
            return unresolved(CFG_UNKNOWN)
        if len(candidates) > 1:
            return unresolved(SEVERAL_TRAIT_IMPLS)
        if candidates:
            spec = candidates[0][1]
            if derived:
                return unresolved(DERIVE_AND_IMPL)
            if name in BLANKET_TRAIT_FNS or name in STD_BLANKET_FNS:
                return unresolved(STD_BLANKET_AMBIGUOUS)
            if spec.trait_target is None or spec.trait_target[0] != "def":  # a trait from outside: its blanket impls are not read
                return unresolved(EXT_TRAIT_IMPL)
            if spec.trait_target[1] in self.wild_traits:
                return unresolved(BLANKET_IMPL)
            if name in self.blanket_fns or not self.trait_visible(body, spec):
                return unresolved(TRAIT_NOT_VISIBLE)
            if candidates[0][0].extra.get("rw") or spec.rewritten:
                return unresolved(ATTR_MACRO)
            return edge(candidates[0][0])
        if derived:
            return unresolved(EXT_TRAIT_SHADOW) if name in self.trait_fn_names else external(DERIVED)
        if name in STD_BLANKET_FNS and name not in self.trait_fn_names:
            return external(STD_BLANKET)
        return unresolved(NO_IMPL)

    def qualified(self, body: Body, bracket, name: str) -> tuple:
        """`<T as Trait>::f(..)` and `<T>::f(..)`."""
        src = body.src
        if body.ctx.edition < 2018:
            return unresolved(EDITION)
        q = bracket.named_children[0] if bracket.named_children else None
        if q is None:
            return unresolved(QUALIFIED_UNKNOWN)
        if q.type != "qualified_type":  # `<T>::f` is `T::f`
            target = self.self_or_type(body, q)
            if target is not None and target[0] == "def":
                return self.assoc(body, target[1], name, written_args(q, src))
            return unresolved(QUALIFIED_UNKNOWN)
        tnode, trnode = q.child_by_field_name("type"), q.child_by_field_name("alias")
        owner = self.self_or_type(body, tnode)
        trait = self.type_target(body.scope, trnode, src, tuple(body.generics))
        if trait is None or owner is None:
            return unresolved(QUALIFIED_UNKNOWN)
        if owner[0] == "generic":
            return unresolved(GENERIC_PATH)
        if trait[0] == "def" and trait[1].kind == "trait":
            if owner[0] == "def":
                if trait[1] in self.wild_traits:  # an impl for every type may be the one that runs, whatever the trait's arguments say
                    return unresolved(BLANKET_IMPL)
                impls = [s for s in self.trait_impls.get(owner[1], []) if s.trait_target == trait]
                if len(impls) > 1:
                    return unresolved(SEVERAL_TRAIT_IMPLS)
                if not impls:
                    return unresolved(NO_IMPL)
                spec = impls[0]
                # One impl of a trait with no parameters is the impl (rustc rejects a second one for the same type), so the
                # tables need not be complete for it; a trait with parameters, or an impl that may not exist, can have
                # another impl that a macro makes (`Conv<u8>` and `Conv<V>`).
                taint = self.impl_taint(body.ctx, owner[1].scope.crate, trait[1].scope.crate)
                if taint and (spec.cfg or generic_names(trait[1].node, trait[1].scope.module_scope.module.src)):
                    return unresolved(IMPLS_INCOMPLETE.format(taint))
                if spec.rewritten:
                    return unresolved(ATTR_MACRO)
                decl = self.trait_decl.get(trait[1], {}).get(name)
                has_default = decl is not None and decl.extra["has_body"]
                if len(spec.methods.get(name, ())) > 1:
                    return unresolved(SEVERAL_TRAIT_IMPLS)
                if name in spec.methods:
                    method = spec.methods[name][0]
                    if method.extra.get("rw"):
                        return unresolved(ATTR_MACRO)
                    return unresolved(CFG_UNKNOWN) if method.extra.get("cfg") and has_default else edge(method)
                if spec.macro_body is not None:  # the macro may define the function: an override of the default
                    return unresolved(IMPL_MACRO)
                if has_default:
                    return unresolved(ATTR_MACRO) if decl.extra.get("rw") else unresolved(SEVERAL) if decl.extra.get("twin") else edge(decl)
                return unresolved(TRAIT_NO_DECL)
            return self.trait_call(trait[1], name)
        if trait[0] == "ext":
            plain = path_segs(trnode, src)[-1]
            if owner[0] == "def":
                impls = [s for s in self.trait_impls.get(owner[1], []) if s.trait_target is not None and s.trait_target[0] == "ext" and s.trait_plain == plain]
                if not impls and name in DERIVE_FNS.get(plain, ()) and plain in self.derives.get(owner[1], ()) and name not in self.trait_fn_names:
                    return external(DERIVED)
                return unresolved(EXT_TRAIT_IMPL if impls or name in self.trait_fn_names else NO_IMPL)
            if name in self.trait_fn_names or name in self.foreign_impl_fns:
                return unresolved(EXT_TRAIT_SHADOW)
            return external(EXT_TRAIT)
        return unresolved(QUALIFIED_UNKNOWN)

    def self_or_type(self, body: Body, tnode) -> tuple | None:
        if tnode.type == "type_identifier" and text(body.src, tnode) == "Self":
            if body.impl is not None:
                return body.impl.self_target
            return None
        return self.type_target(body.scope, tnode, body.src, tuple(body.generics))

    # ---- reading bodies
    def read_bodies(self, parsed: dict[str, tuple]) -> None:
        for spec in self.bodies:
            ctx = spec.scope.crate
            node = spec.node
            root = node.child_by_field_name("body") if node.type == "function_item" else node
            if root is None:
                continue
            body = Body(spec.scope, ctx, spec.src, spec.impl, spec.trait, frozenset(spec.generics), scan_locals(node, spec.src), spec.rewritten or spec.scope.rewritten)
            self.visit(body, root, spec.module.file, spec.caller, 0, 0, False, False)

    def macro_is_std(self, body: Body, name: str, mnode) -> bool:
        """Whether `name!` is the standard macro: no repo macro_rules! is called so, and no `use` brings in another."""
        if name in self.repo_macros or body.ctx.macro_use_extern:
            return False
        if mnode.type != "identifier":
            return path_segs(mnode, body.src)[0] in ("std", "core", "alloc")
        s = body.scope
        while s is not None:
            if name in s.uses or s.macro_use_mod:
                return False
            s = s.outer
        return not (body.scope.module_scope.uses.get(name))

    def visit(self, body: Body, root, file: str, caller: tuple, line0: int, offset: int, in_macro: bool, tainted: bool) -> None:
        """Every call in a function's code. `offset` and `line0` place the code of a macro's arguments, which were
        parsed again as text of their own, back in the file. `tainted` is code in a block of such arguments that
        declares items or invokes a macro: the block has no scope here, so what its calls name is not known."""
        src, ctx = body.src, body.ctx
        stack: list[tuple[object, Scope, bool]] = [(root, body.scope, tainted)]
        while stack:
            n, scope, taint = stack.pop()
            t = n.type
            if n is not root and t in NESTED_ITEMS:
                continue
            if t == "block":
                if not in_macro:
                    scope = ctx.blocks.get((file, n.start_byte), scope)
                elif any(c.type in ITEM_NODES or statement_macro(c) is not None for c in n.named_children):
                    taint = True
            if t == "call_expression":
                local = body if scope is body.scope else Body(scope, ctx, src, body.impl, body.trait, body.generics, body.locals, body.rewritten)
                result, written_as, name_end = self.classify(local, n)
                if taint:
                    result = unresolved(MACRO_BLOCK_ITEMS)
                elif body.rewritten:
                    result = unresolved(ATTR_MACRO)
                self.sites[(file, offset + n.start_byte, offset + n.end_byte)][id(scope.module_scope)] = (
                    result, caller, line0 + n.start_point[0] + 1, written_as, offset + name_end,
                )
            elif t == "macro_invocation":
                self.visit_macro(Body(scope, ctx, src, body.impl, body.trait, body.generics, body.locals, body.rewritten), n, file, caller, line0, offset, taint)
                continue
            stack.extend((child, scope, taint) for child in n.named_children)

    def visit_macro(self, body: Body, node, file: str, caller: tuple, line0: int, offset: int, tainted: bool = False) -> None:
        src = body.src
        mnode = node.child_by_field_name("macro")
        tt = next((c for c in node.named_children if c.type == "token_tree"), None)
        if mnode is None or tt is None:
            return
        name = path_segs(mnode, src)[-1]
        if name in QUOTE_MACROS and name not in self.repo_macros:
            self.quoted += 1  # generated code for another crate, not a call of this one
            return
        if (name in STD_EXPR_MACROS or name in STD_ARRAY_MACROS) and self.macro_is_std(body, name, mnode):
            inner = src[tt.start_byte + 1 : tt.end_byte - 1]
            pre, post = (b"fn __w() { __f(", b"); }") if name in STD_EXPR_MACROS else (b"fn __w() { [", b"]; }")
            wrapped = pre + inner + post
            tree = load_parser().parse(wrapped)
            if not tree.root_node.has_error:
                fn = tree.root_node.named_children[0]
                block = fn.child_by_field_name("body")
                stmt = block.named_children[0] if block is not None and block.named_children else None
                start = stmt.named_children[0] if stmt is not None and stmt.named_children else None
                if start is not None and start.type == "call_expression":
                    start = start.child_by_field_name("arguments")
                if start is not None and name == "matches" and start.named_children:
                    start = start.named_children[0]  # the pattern after the comma runs no code
                if start is not None:
                    sub = Body(body.scope, body.ctx, wrapped, body.impl, body.trait, body.generics, body.locals | scan_locals(fn, wrapped), body.rewritten)
                    self.visit(sub, start, file, caller, line0 + tt.start_point[0], offset + tt.start_byte + 1 - len(pre), True, tainted)
                return
            reason = STD_MACRO_BODY
        else:
            reason = MACRO_BODY
        if tainted:
            reason = MACRO_BLOCK_ITEMS
        elif body.rewritten:
            reason = ATTR_MACRO
        stack = [tt]
        while stack:
            n = stack.pop()
            kids = n.children
            for i, c in enumerate(kids):
                if c.type == "token_tree":
                    stack.append(c)
                    if c.children and c.children[0].type == "(" and i > 0 and kids[i - 1].type == "identifier":
                        ident = kids[i - 1]
                        self.sites[(file, offset + ident.start_byte, offset + ident.end_byte)][id(body.scope.module_scope)] = (
                            unresolved(reason), caller, line0 + ident.start_point[0] + 1, text(src, ident), offset + ident.end_byte,
                        )


    # ---- the graph
    def resolved_sites(self):
        """(file, start byte, end byte, result, caller key, line, callee as written, byte where its last name ends) of
        every call site, in file order, with the one result the crates that read the site agree on (SHARED_FILE when
        they differ)."""
        for (file, start, end), by_crate in sorted(self.sites.items()):
            results = {r[0] for r in by_crate.values()}
            _, caller, line, written_as, name_end = next(iter(by_crate.values()))
            yield file, start, end, unresolved(SHARED_FILE) if len(results) > 1 else next(iter(results)), caller, line, written_as, name_end

    def finish(self, repo_files: dict[str, int], graph: Graph) -> None:
        """Symbols with their final ids, then every call site resolved across the crates that share it."""
        groups: dict[str, list[tuple]] = defaultdict(list)
        for key, (base, _, _, _) in self.raw.items():
            groups[base].append(key)
        for base, keys in groups.items():
            for i, key in enumerate(sorted(keys)):
                self.final[key] = base if i == 0 else f"{base}#{i + 1}"
        for key, (_, kind, file, node) in self.raw.items():
            sid = self.final[key]
            graph.symbols[sid] = Symbol(sid, kind, file, node.start_point[0] + 1, node.end_point[0] + 1)
        for file, lines in repo_files.items():
            mid = module_id(file)
            graph.symbols.setdefault(mid, Symbol(mid, "module", file, 1, lines))
        external_reasons: Counter = Counter()
        for file, _, _, result, caller, line, written_as, _ in self.resolved_sites():
            caller_id = module_id(caller[0]) if caller[1] == -1 else self.final[caller]
            kind = result[0]
            if kind == "edge":
                graph.edges.append(Edge(caller_id, self.final[result[1]], file, line, "call"))
                for o in result[2]:
                    graph.edges.append(Edge(caller_id, self.final[o], file, line, "override"))
            elif kind == "external":
                graph.external_calls += 1
                external_reasons[result[1]] += 1
            elif kind == "unresolved":
                graph.unresolved.append(Unresolved(caller_id, file, line, written_as, result[1]))
        graph.external_reasons = dict(external_reasons.most_common())


# ---- the front end the registry asks for


def reads_as_source(path: Path, profile: LanguageProfile, repo: Path | None = None) -> bool:
    """False for a `.rs` file under a cargo `target` directory (a directory called target beside a Cargo.toml), which
    holds build output: generated sources that no `mod` of the repo reaches."""
    for parent in path.parents:
        if parent.name == "target" and (parent.parent / "Cargo.toml").is_file():
            return False
    return True


NotInstalled = RustNotInstalled


def index_files(repo: Path, files: list[Path], skipped: list[str], profile: LanguageProfile = RUST) -> Graph:
    """The call graph of a Rust repo: its crates, their module trees, and the calls that name a path."""
    return read_repo(repo, files, skipped)[1]


def read_repo(repo: Path, files: list[Path], skipped: list[str]) -> tuple[Index, Graph]:
    """The index of a Rust repo (what its call sites resolved to, one by one) and the graph made from it."""
    parser = load_parser()
    walked = {p.relative_to(repo).as_posix() for p in files}
    problems: list[str] = []
    parsed: dict[str, tuple] = {}
    with_errors: set[str] = set()

    def parse(rel: str):
        if rel not in parsed:
            try:
                src = (repo / rel).read_bytes()
                src, tree = blank_comments(parser, src, parser.parse(src))
            except Exception:  # noqa: BLE001 (a file the parser cannot read must cost that file, not the index)
                problems.append(rel)
                parsed[rel] = None
                return None
            if tree.root_node.has_error:
                with_errors.add(rel)
            parsed[rel] = (src, tree.root_node, tree)
        found = parsed[rel]
        return None if found is None else (found[0], found[1])

    workspace = rs_modules.load_workspace(repo, files)
    problems += workspace.problems
    unread: list[str] = []
    trees = []
    for crate in workspace.crates:
        tree = rs_modules.build_tree(repo, crate, walked, parse)
        unread += tree.problems
        trees.append((crate, tree))
    index = Index(trees)
    read = {m.file for _, tree in trees for m in tree.modules}
    index.read_bodies(parsed)
    graph = Graph()
    index.finish({f: parsed[f][0].count(b"\n") + 1 for f in sorted(read) if parsed.get(f)}, graph)
    for rel in sorted(walked - read):
        unread.append(f"{rel} (no `mod` declaration reaches it from a crate root, so it is not read)")
    graph.parse_errors = sorted(set(problems) | with_errors)
    graph.unread = sorted(set(unread))
    graph.skipped_dirs = skipped
    graph.modules.update({f: module_id(f) for f in sorted(read) if parsed.get(f)})
    graph._index()  # the edges were added after the graph was made: its caller and callee tables are built from them
    return index, graph
