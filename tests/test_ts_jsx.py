"""JSX render edges (stage 4): two kinds, `renders` and `renderer`, read from small .tsx snippets.

`renders` is the creator: the function whose body writes `<Child/>`. `renderer` is the component React was
rendering when it ran Child, and it is an edge only when the source proves it. These tests index a few files with
the TYPESCRIPT profile, so they need tree-sitter (the `typescript` extra) and are skipped without it.
"""

import pytest

pytest.importorskip("tree_sitter", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")
pytest.importorskip("tree_sitter_typescript", reason="TypeScript support is not installed (pip install 'ripple-code[typescript]')")

from ripple import queries  # noqa: E402
from ripple.cache import load_graph  # noqa: E402
from ripple.jsx_facts import JSX_NO_RENDERER, JSX_TAG_UNRESOLVED  # noqa: E402
from ripple.indexer import build_graph  # noqa: E402
from ripple.profile import TYPESCRIPT  # noqa: E402

LEAF = "export function Leaf() { return <i />; }\n"
CHILD = "export function Child() { return <i />; }\n"


def index(make_repo, files):
    return build_graph(make_repo(files), TYPESCRIPT)


def pairs(graph, kind):
    return {(e.caller, e.callee) for e in graph.edges if e.kind == kind}


def creators(graph):
    return pairs(graph, "renders")


def renderers(graph):
    return pairs(graph, "renderer")


def tag_unresolved(graph):
    return {(u.caller, u.text) for u in graph.unresolved if u.reason.startswith(JSX_TAG_UNRESOLVED)}


def renderer_unresolved(graph):
    return {(u.caller, u.text) for u in graph.unresolved if u.reason.startswith(JSX_NO_RENDERER)}


# ---- the forms of an element

def test_every_form_of_a_component_element_is_a_creator_edge_from_the_function_that_wrote_it(make_repo):
    graph = index(
        make_repo,
        {
            "src/leaf.tsx": LEAF + "export function Item<T>(props: { rows: T[] }) { return <b />; }\n",
            "src/page.tsx": """
            import { Leaf, Item } from './leaf';
            export function Page(props: any) {
              return (
                <>
                  <Leaf />
                  <Item<number> rows={[]} {...props} />
                </>
              );
            }
            export const Arrow = () => <Leaf></Leaf>;
            """,
        },
    )
    assert creators(graph) == {
        ("src/page:Page", "src/leaf:Leaf"),
        ("src/page:Page", "src/leaf:Item"),
        ("src/page:Arrow", "src/leaf:Leaf"),
    }
    assert tag_unresolved(graph) == set()


def test_an_intrinsic_tag_is_never_a_component(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.tsx": """
            function widget() { return <i />; }
            export function Page() {
              return <div><my-element /><svg:rect /><widget /></div>;
            }
            """
        },
    )
    assert creators(graph) == set() and tag_unresolved(graph) == set()


def test_an_element_in_a_conditional_a_map_callback_and_a_render_prop_belongs_to_the_function_around_it(make_repo):
    graph = index(
        make_repo,
        {
            "src/leaf.tsx": LEAF + "export function Row() { return <li />; }\nexport function Other() { return <u />; }\n",
            "src/page.tsx": """
            import { Leaf, Row, Other } from './leaf';
            export function Page({ items, show }: { items: number[]; show: boolean }) {
              return (
                <ul>
                  {show && <Leaf />}
                  {items.map((n) => <Row key={n} />)}
                  <Slot render={() => <Other />} />
                </ul>
              );
            }
            function Slot(props: any) { return null; }
            """,
        },
    )
    assert creators(graph) == {
        ("src/page:Page", "src/leaf:Leaf"),
        ("src/page:Page", "src/leaf:Row"),
        ("src/page:Page", "src/leaf:Other"),
        ("src/page:Page", "src/page:Slot"),
    }


# ---- which name a tag is

def test_a_local_function_named_like_an_import_hides_the_import(make_repo):
    graph = index(
        make_repo,
        {
            "src/child.tsx": CHILD,
            "src/p.tsx": """
            import { Child } from './child';
            export function Outer() {
              const Child = () => <b />;
              return <Child />;
            }
            export function Inner({ Child }: any) { return <Child />; }
            export function Fine() { return <Child />; }
            """,
        },
    )
    assert creators(graph) == {("src/p:Outer", "src/p:Outer.Child"), ("src/p:Fine", "src/child:Child")}
    assert tag_unresolved(graph) == {("src/p:Inner", "Child")}


def test_a_name_declared_in_a_block_is_that_blocks_own(make_repo):
    graph = index(
        make_repo,
        {
            "src/child.tsx": CHILD,
            "src/p.tsx": """
            import { Child } from './child';
            export function B(flag: any) {
              if (flag) {
                const Child = flag.as;
                return <Child />;
              }
              return <Child />;
            }
            """,
        },
    )
    assert [(e.caller, e.callee, e.line) for e in graph.edges if e.kind == "renders"] == [("src/p:B", "src/child:Child", 8)]
    assert {(u.caller, u.text, u.line) for u in graph.unresolved if u.reason.startswith(JSX_TAG_UNRESOLVED)} == {("src/p:B", "Child", 6)}


def test_a_tag_that_is_a_variable_of_unknown_value_stays_unresolved(make_repo):
    graph = index(
        make_repo,
        {"src/p.tsx": "export function Page(props: any) {\n  const Comp = props.as;\n  return <Comp />;\n}\n"},
    )
    assert creators(graph) == set()
    assert tag_unresolved(graph) == {("src/p:Page", "Comp")}


def test_a_tag_is_found_through_a_barrel_and_a_namespace_import(make_repo):
    graph = index(
        make_repo,
        {
            "src/ui/Panel.tsx": "export function Panel() { return <section />; }\n",
            "src/ui/index.ts": "export { Panel } from './Panel';\n",
            "src/a.tsx": "import { Panel } from './ui';\nexport function A() { return <Panel />; }\n",
            "src/b.tsx": "import * as ui from './ui';\nexport function B() { return <ui.Panel />; }\n",
            "src/c.tsx": "import * as lib from 'some-lib';\nexport function C() { return <lib.Panel />; }\n",
        },
    )
    assert creators(graph) == {("src/a:A", "src/ui/Panel:Panel"), ("src/b:B", "src/ui/Panel:Panel")}
    assert tag_unresolved(graph) == set()


def test_a_member_of_this_is_not_followed(make_repo):
    graph = index(
        make_repo,
        {
            "src/c.tsx": """
            import { Component } from 'react';
            export class C extends Component {
              Foo = () => <i />;
              render() { return <this.Foo />; }
            }
            """
        },
    )
    assert creators(graph) == set()


def test_a_component_from_a_package_is_outside_the_repo_not_unresolved(make_repo):
    graph = index(
        make_repo,
        {"src/p.tsx": "import { Link } from 'router';\nexport function Page() { return <Link to='/' />; }\n"},
    )
    assert creators(graph) == set() and graph.unresolved == []
    assert graph.external_calls == 1


# ---- the renderer: only what the source proves

def test_a_component_returned_directly_is_rendered_by_the_one_that_wrote_it_when_react_renders_that_one(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.tsx": """
            export function Leaf() { return <i />; }
            export function Row() { return <li><Leaf /></li>; }
            export function Page({ ok }: { ok: boolean }) { return ok ? <Row /> : null; }
            export function App() { return <Page ok />; }
            """
        },
    )
    assert creators(graph) == {("src/m:Row", "src/m:Leaf"), ("src/m:Page", "src/m:Row"), ("src/m:App", "src/m:Page")}
    # Row and Page are components (each is a tag somewhere); App is a tag nowhere, so what renders Page is not proven
    assert renderers(graph) == {("src/m:Row", "src/m:Leaf"), ("src/m:Page", "src/m:Row")}
    assert ("src/m:App", "Page") in renderer_unresolved(graph)


def test_a_function_that_is_also_called_is_not_known_to_be_rendered_as_a_component(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.tsx": """
            export function Leaf() { return <i />; }
            export function helper() { return <Leaf />; }
            export function Dual() { return <Leaf />; }
            export function call() { return Dual(); }
            export function Plain() { return <div><Dual /></div>; }
            export function Top() { return <Plain />; }
            """
        },
    )
    assert {("src/m:helper", "src/m:Leaf"), ("src/m:Dual", "src/m:Leaf")} <= creators(graph)
    # helper is a tag nowhere, and Dual is a tag but is also called as a function: neither is known to be rendered as one
    assert renderers(graph) == {("src/m:Plain", "src/m:Dual")}


def test_children_passed_to_a_component_that_places_them_are_rendered_by_it_not_by_their_creator(make_repo):
    graph = index(
        make_repo,
        {
            "src/kids.tsx": """
            export function A() { return <i />; }
            export function B() { return <i />; }
            export function C() { return <i />; }
            export function D() { return <i />; }
            export function E() { return <i />; }
            """,
            "src/layout.tsx": """
            import type { ReactNode } from 'react';
            export function Layout({ children }: { children?: ReactNode }) { return <main>{children}</main>; }
            export function Plain(props: { children?: ReactNode }) { return <div><p>{props.children}</p></div>; }
            export function Renamed({ children: kids }: { children?: ReactNode }) { return <>{kids}</>; }
            export function Gate({ open, children }: { open: boolean; children?: ReactNode }) {
              return <div>{open && children}</div>;
            }
            export function Ignores({ children }: { children?: ReactNode }) { return <div />; }
            """,
            "src/page.tsx": """
            import { A, B, C, D, E } from './kids';
            import { Layout, Plain, Renamed, Gate, Ignores } from './layout';
            export function Page() {
              return (
                <section>
                  <Layout><A /></Layout>
                  <Plain><span><B /></span></Plain>
                  <Renamed><C /></Renamed>
                  <Gate open><D /></Gate>
                  <Ignores><E /></Ignores>
                </section>
              );
            }
            """,
        },
    )
    assert {("src/page:Page", f"src/kids:{n}") for n in "ABCDE"} <= creators(graph)
    assert not any(c == "src/page:Page" and e.endswith(("A", "B", "C", "D", "E")) for c, e in renderers(graph))
    assert {(c, e) for c, e in renderers(graph) if e.startswith("src/kids:")} == {
        ("src/layout:Layout", "src/kids:A"),
        ("src/layout:Plain", "src/kids:B"),
        ("src/layout:Renamed", "src/kids:C"),
        ("src/layout:Gate", "src/kids:D"),
    }
    assert ("src/page:Page", "E") in renderer_unresolved(graph)


def test_children_placed_by_a_wrapper_that_hands_them_to_another_component_follow_that_component(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.tsx": """
            import type { ReactNode } from 'react';
            export function Leaf() { return <i />; }
            export function Inner({ children }: { children?: ReactNode }) { return <div>{children}</div>; }
            export function Outer({ children }: { children?: ReactNode }) { return <Inner>{children}</Inner>; }
            export function Page() { return <Outer><Leaf /></Outer>; }
            """
        },
    )
    assert {(c, e) for c, e in renderers(graph) if e == "src/m:Leaf"} == {("src/m:Inner", "src/m:Leaf")}


def test_children_that_pass_through_code_ripple_does_not_read_have_no_renderer_edge(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.tsx": """
            import { Children, cloneElement } from 'react';
            import styled from 'styled-components';
            const Box = styled.ul``;
            export function Leaf() { return <i />; }
            export function Cloner({ children }: any) { return <div>{Children.map(children, (c) => cloneElement(c))}</div>; }
            export function Stored({ children }: any) { const kept = children; return <div>{kept}</div>; }
            export function Late({ children }: any) { return <div onClick={() => console.log(children)} />; }
            export function Page() {
              return <><Cloner><Leaf /></Cloner><Stored><Leaf /></Stored><Late><Leaf /></Late><Box><Leaf /></Box></>;
            }
            """
        },
    )
    assert ("src/m:Page", "src/m:Leaf") in creators(graph)
    assert {(c, e) for c, e in renderers(graph) if e == "src/m:Leaf"} == set()
    assert ("src/m:Page", "Leaf") in renderer_unresolved(graph)


def test_a_function_passed_as_a_prop_and_called_where_the_component_places_its_result_is_rendered_by_that_component(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.tsx": """
            import type { ReactNode } from 'react';
            export function Item(props: { n: number }) { return <b />; }
            export function Late(props: { n: number }) { return <b />; }
            export function Typed(props: { n: number }) { return <b />; }
            export function List({ rows, render }: { rows: number[]; render: (n: number) => ReactNode }) {
              return <ul>{rows.map((r) => <li key={r}>{render(r)}</li>)}</ul>;
            }
            export function Direct(props: { render: () => ReactNode }) { return <div>{props.render()}</div>; }
            export function Handler({ render }: { render: () => ReactNode }) { return <button onClick={() => render()} />; }
            export function Unknown({ rows, render }: any) { return <ul>{rows.map((r: number) => <li>{render(r)}</li>)}</ul>; }
            export function Page() {
              return (
                <>
                  <List rows={[1]} render={(n) => <Item n={n} />} />
                  <Direct render={() => <Typed n={1} />} />
                  <Handler render={() => <Late n={1} />} />
                  <Unknown rows={[1]} render={(n: number) => <Late n={n} />} />
                </>
              );
            }
            """
        },
    )
    assert {("src/m:Page", f"src/m:{n}") for n in ("Item", "Typed", "Late")} <= creators(graph)
    assert {(c, e) for c, e in renderers(graph) if e in ("src/m:Item", "src/m:Typed", "src/m:Late")} == {
        ("src/m:List", "src/m:Item"),
        ("src/m:Direct", "src/m:Typed"),
    }


def test_react_fragments_and_suspense_render_their_children_where_they_stand(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.tsx": """
            import React, { Fragment, Suspense } from 'react';
            export function A() { return <i />; }
            export function B() { return <i />; }
            export function C() { return <i />; }
            export function Page() {
              return <React.Fragment><A /><Suspense fallback={null}><B /></Suspense><Fragment><C /></Fragment></React.Fragment>;
            }
            export function App() { return <Page />; }
            """
        },
    )
    assert {(c, e) for c, e in renderers(graph) if e in ("src/m:A", "src/m:B", "src/m:C")} == {
        ("src/m:Page", "src/m:A"),
        ("src/m:Page", "src/m:B"),
        ("src/m:Page", "src/m:C"),
    }
    assert graph.external_calls == 0  # a fragment is not a component from outside the repo


def test_a_function_handed_on_by_name_may_be_called_by_what_gets_it_so_it_is_not_known_to_be_a_component(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.tsx": """
            import { Route } from 'router';
            export function Leaf() { return <i />; }
            export function Home() { return <Leaf />; }
            export function Other() { return <Leaf />; }
            export function App() { return <><Route render={Home} /><Home /><Other /></>; }
            """
        },
    )
    assert renderers(graph) == {("src/m:Other", "src/m:Leaf")}  # Home may have been called as a function by Route


def test_a_file_the_parser_had_to_recover_from_creates_but_places_nothing(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.tsx": """
            export function Leaf() { return <i />; }
            export function Row() { return <li><Leaf /></li>; }
            export function Page() { return <Row />; }
            export const broken = ;
            """
        },
    )
    assert graph.parse_errors == ["src/m.tsx"]
    assert {("src/m:Row", "src/m:Leaf"), ("src/m:Page", "src/m:Row")} <= creators(graph)
    assert renderers(graph) == set()
    assert any("syntax error" in u.reason for u in graph.unresolved)


# ---- class components and wrappers, which the fixture does not have

def test_a_class_component_creates_from_render_and_is_the_renderer_of_what_render_returns(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.tsx": """
            import { Component, PureComponent } from 'react';
            export function Inner() { return <i />; }
            export class Box extends Component { render() { return <div><Inner /></div>; } }
            export class Pure extends PureComponent { render() { return <Inner />; } }
            class Base {}
            export class NotReact extends Base { render() { return <Inner />; } }
            export function Page() { return <><Box /><Pure /><NotReact /></>; }
            """
        },
    )
    assert creators(graph) == {
        ("src/m:Box.render", "src/m:Inner"),
        ("src/m:Pure.render", "src/m:Inner"),
        ("src/m:NotReact.render", "src/m:Inner"),
        ("src/m:Page", "src/m:Box"),
        ("src/m:Page", "src/m:Pure"),
        ("src/m:Page", "src/m:NotReact"),
    }
    assert {(c, e) for c, e in renderers(graph) if e == "src/m:Inner"} == {("src/m:Box", "src/m:Inner"), ("src/m:Pure", "src/m:Inner")}


def test_memo_and_forward_ref_resolve_only_when_they_wrap_a_function_ripple_can_see(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.tsx": """
            import React, { memo, forwardRef, lazy } from 'react';
            import styled from 'styled-components';
            import { outside } from 'elsewhere';
            function Raw() { return <i />; }
            export const Wrapped = memo(Raw);
            export const Inline = React.memo(function Inline() { return <b />; });
            export const Fwd = forwardRef((props: any, ref: any) => <u />);
            export const Lz = lazy(() => import('./x'));
            export const Foreign = memo(outside);
            export const Styled = styled.ul``;
            export function Page() {
              return <><Wrapped /><Inline /><Fwd /><Lz /><Foreign /><Styled /></>;
            }
            """
        },
    )
    assert creators(graph) == {
        ("src/m:Page", "src/m:Raw"),
        ("src/m:Page", "src/m:Inline"),
        ("src/m:Page", "src/m:Fwd"),
    }
    assert {t for _, t in tag_unresolved(graph)} == {"Lz", "Foreign", "Styled"}


def test_a_styled_wrapper_between_a_parent_and_a_child_leaves_the_renderer_unresolved(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.tsx": """
            import styled from 'styled-components';
            const List = styled.ul``;
            export function Leaf() { return <i />; }
            export function Page() { return <List><Leaf /></List>; }
            export function App() { return <Page />; }
            """
        },
    )
    assert ("src/m:Page", "src/m:Leaf") in creators(graph)
    assert renderers(graph) == set()


# ---- the edge kind in what a query says

def test_each_query_labels_the_kind_of_a_render_edge_and_a_filter_selects_kinds(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.tsx": """
            export function util() { return 1; }
            export function Leaf() { return <i />; }
            export function Row() { util(); return <li><Leaf /></li>; }
            export function Page() { return <Row />; }
            """
        },
    )
    entry = queries.callers(graph, "src/m:Leaf")["callers"]
    assert [(e["symbol"], e["edge_kinds"]) for e in entry] == [("src/m:Row", ["renders", "renderer"])]
    only = queries.callers(graph, "src/m:Leaf", kinds={"renders"})["callers"]
    assert [e["edge_kinds"] for e in only] == [["renders"]]
    assert queries.callers(graph, "src/m:Leaf", kinds={"calls"})["callers"] == []
    # a plain call has no label, as before
    plain = queries.callers(graph, "src/m:util")["callers"]
    assert plain and all("edge_kinds" not in e for e in plain)
    deeper = queries.impact(graph, "src/m:Leaf")
    assert [(e["symbol"], e["edge_kinds"]) for f in deeper["by_file"].values() for e in f] == [
        ("src/m:Row", ["renders", "renderer"]),
        ("src/m:Page", ["renders"]),
    ]
    assert queries.impact(graph, "src/m:Leaf", kinds={"calls"})["affected"] == 0
    callees = queries.callees(graph, "src/m:Row")
    assert {e["symbol"]: e["edge_kinds"] for e in callees["callees"] if "edge_kinds" in e} == {"src/m:Leaf": ["renders", "renderer"]}
    hop = queries.path(graph, "src/m:Page", "src/m:Leaf")["path"]
    assert [h.get("edge_kinds") for h in hop] == [None, ["renders"], ["renders", "renderer"]]
    assert queries.path(graph, "src/m:Page", "src/m:Leaf", kinds={"calls"})["path"] is None


def test_a_graph_with_render_edges_survives_the_cache(make_repo, tmp_path):
    repo = make_repo({"src/m.tsx": "export function Leaf() { return <i />; }\nexport function Row() { return <li><Leaf /></li>; }\nexport function Page() { return <Row />; }\n"})
    first, from_cache = load_graph(repo, tmp_path / "c", profile=TYPESCRIPT)
    again, from_cache_again = load_graph(repo, tmp_path / "c", profile=TYPESCRIPT)
    assert (from_cache, from_cache_again) == (False, True)
    assert again.to_dict() == first.to_dict()
    assert creators(again) and renderers(again)


# ---- what a review of 74 projects against React's own fiber tree found: each of these had a false renderer edge

def renderer_to(graph, name):
    return {(c, e) for c, e in renderers(graph) if e.endswith(":" + name)}


def test_a_class_is_the_renderer_only_when_react_instantiates_it_never_the_base_that_defines_render(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.tsx": """
            import { Component } from 'react';
            export function Leaf() { return <i />; }
            class Base extends Component<any> { render() { return <i><Leaf /></i>; } }
            class Fancy extends Base {}
            class Plain extends Base {}
            export default function App() { return <div><Fancy /><Plain /></div>; }
            """
        },
    )
    assert renderer_to(graph, "Leaf") == {("src/m:Fancy", "src/m:Leaf"), ("src/m:Plain", "src/m:Leaf")}
    assert ("src/m:Base.render", "src/m:Leaf") in creators(graph)  # the creator is still the method that wrote it


def test_a_class_that_is_rendered_by_its_own_tag_is_still_the_renderer(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.tsx": """
            import { Component } from 'react';
            export function Leaf() { return <i />; }
            class Box extends Component<any> { render() { return <Leaf />; } }
            export default function App() { return <Box />; }
            """
        },
    )
    assert renderer_to(graph, "Leaf") == {("src/m:Box", "src/m:Leaf")}


def test_a_class_whose_render_is_called_by_hand_or_that_no_element_names_is_not_a_proven_renderer(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.tsx": """
            import { Component } from 'react';
            export function A() { return <i />; }
            export function B() { return <i />; }
            export function C() { return <i />; }
            export function D() { return <i />; }
            class Unnamed extends Component<any> { render() { return <A />; } }
            class Again extends Component<any> { render() { return <B />; } again() { return this.render(); } }
            class Typed extends Component<any> { render() { return <C />; } }
            class Sup extends Component<any> { render() { return <D />; } }
            class Sub extends Sup { render() { return super.render(); } }
            export function Host({ c }: { c: any }) { return <div>{c.render()}</div>; }
            export function typed() { return new Typed({}).render(); }
            export default function App() { return <><Host c={new Unnamed({})} /><Again /><Typed /><Sub /></>; }
            """
        },
    )
    assert {e for _, e in renderers(graph)} & {"src/m:A", "src/m:B", "src/m:C", "src/m:D"} == set()
    assert {("src/m:Unnamed.render", "src/m:A"), ("src/m:Again.render", "src/m:B")} <= creators(graph)


def test_a_tag_that_is_bound_again_is_unresolved_for_both_kinds_even_when_it_was_first_a_memo(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.tsx": """
            import { memo } from 'react';
            export function Leaf() { return <i />; }
            function A({ children }: any) { return <section>{children}</section>; }
            function B() { return <b />; }
            function C({ children }: any) { return <section>{children}</section>; }
            function D({ children }: any) { return <section>{children}</section>; }
            let Btn = memo(A);
            Btn = B as any;
            let Row = memo(C);
            export function setRow(c: any) { Row = c; }
            let Inline = memo(function Inline({ children }: any) { return <div>{children}</div>; });
            Inline = B as any;
            export default function App() { return <div><Btn><Leaf /></Btn><Row><Leaf /></Row><Inline><Leaf /></Inline></div>; }
            """
        },
    )
    assert {e for c, e in creators(graph) if c == "src/m:App"} == {"src/m:Leaf"}
    assert renderer_to(graph, "Leaf") == set()
    assert {t for _, t in tag_unresolved(graph)} == {"Btn", "Row", "Inline"}


def test_a_const_memo_that_is_bound_once_still_resolves_to_the_function_it_wraps(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.tsx": """
            import { memo } from 'react';
            export function Leaf() { return <i />; }
            function A({ children }: any) { return <section>{children}</section>; }
            const Btn = memo(A);
            export default function App() { return <div><Btn><Leaf /></Btn></div>; }
            """
        },
    )
    assert ("src/m:App", "src/m:A") in creators(graph)
    assert renderer_to(graph, "Leaf") == {("src/m:A", "src/m:Leaf")}


def test_a_function_name_bound_again_is_unresolved_as_a_tag_and_as_a_call(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.tsx": """
            export function one() { return 1; }
            export function two() { return 2; }
            let pick = one;
            pick = two;
            export function Leaf() { return <i />; }
            let Swap = () => <Leaf />;
            Swap = () => <b />;
            export function use() { pick(); return <Swap />; }
            """
        },
    )
    assert not {(c, e) for c, e in edges_of(graph) if c == "src/m:use"}
    assert tag_unresolved(graph) == {("src/m:use", "Swap")}


def edges_of(graph):
    return {(e.caller, e.callee) for e in graph.edges}


def test_a_prop_assigned_anywhere_in_the_component_is_not_what_the_caller_passed_whatever_the_order(make_repo):
    bodies = {
        "Loop": "for (let i = 0; i < 2; i++) { if (i === 1) return <div>{children}</div>; children = <Other />; } return null;",
        "Hoisted": "swap(); return <div>{children}</div>; function swap() { children = <Other />; }",
        "Props": "while (props.n < 1) { props = { ...props, children: <Other /> }; } return <div>{props.children}</div>;",
        "Delete": "delete props.children; return <div>{props.children}</div>;",
        "Assign": "Object.assign(props, { children: <Other /> }); return <div>{props.children}</div>;",
        "Member": "props.children = <Other />; return <div>{props.children}</div>;",
        "Redeclared": "var props: any = { children: <Other /> }; return <div>{props.children}</div>;",
        "Update": "const f = () => { children = null; }; return <div>{children}</div>;",
    }
    components = "\n".join(
        f"export function {name}({'props: any' if name in ('Props', 'Delete', 'Assign', 'Member', 'Redeclared') else '{ children }: any'}) {{ {body} }}"
        for name, body in bodies.items()
    )
    uses = "".join(f"<{name} n={{0}}><Leaf /></{name}>" for name in bodies)
    graph = index(
        make_repo,
        {
            "src/m.tsx": f"export function Leaf() {{ return <i />; }}\nexport function Other() {{ return <i />; }}\n{components}\n"
            f"export default function App() {{ return <>{uses}</>; }}\n"
        },
    )
    assert renderer_to(graph, "Leaf") == set(), renderer_to(graph, "Leaf")


def test_a_prop_that_is_not_assigned_still_passes_through_whatever_else_the_component_does(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.tsx": """
            export function Leaf() { return <i />; }
            export function Wrap({ children, n }: any) {
              let kids = children;
              let count = n;
              for (let i = 0; i < 2; i++) { count = count + i; kids = null; }
              const helper = (props: any) => { props = {}; return props; };
              return <div>{children}</div>;
            }
            export default function App() { return <Wrap n={1}><Leaf /></Wrap>; }
            """
        },
    )
    assert renderer_to(graph, "Leaf") == {("src/m:Wrap", "src/m:Leaf")}


def test_the_children_of_an_element_that_cloneelement_or_children_map_is_given_are_not_proven_rendered(make_repo):
    graph = index(
        make_repo,
        {
            "src/m.tsx": """
            import React, { cloneElement, Children as C } from 'react';
            export function Leaf() { return <i />; }
            export function Other() { return <i />; }
            export function Deep() { return <i />; }
            export function Fine() { return <i />; }
            export function Wrap({ children }: any) { return <section>{children}</section>; }
            export function Mid({ children }: any) { return <p>{children}</p>; }
            export function A() { return cloneElement(<Wrap><Leaf /></Wrap>, {}, <Other />); }
            export function B() { return React.Children.map(<Wrap><Deep /></Wrap>, (c) => c); }
            export function D() { return C.toArray(<Wrap><Mid><Deep /></Mid></Wrap>); }
            export default function App() { return <Wrap><Fine /></Wrap>; }
            """
        },
    )
    assert {("src/m:A", "src/m:Wrap"), ("src/m:A", "src/m:Leaf"), ("src/m:A", "src/m:Other")} <= creators(graph)
    assert renderer_to(graph, "Leaf") == set() and renderer_to(graph, "Deep") == set()
    assert renderer_to(graph, "Fine") == {("src/m:Wrap", "src/m:Fine")}  # an element returned as usual is as proven as before


NON_REACT_FILE = """
export function Leaf() { return <i />; }
export function Layout({ children }: any) { return <main>{children}</main>; }
export function Page() { return <Layout><Leaf /></Layout>; }
export function App() { return <Page />; }
"""


@pytest.mark.parametrize("header", ["/** @jsx h */", "/** @jsxImportSource preact */", "/** @jsxFrag Frag */"])
def test_a_file_whose_jsx_is_compiled_by_another_runtime_has_creator_edges_only(make_repo, header):
    graph = index(make_repo, {"src/m.tsx": header + NON_REACT_FILE})
    assert {("src/m:Page", "src/m:Layout"), ("src/m:Layout", "src/m:Leaf")} - creators(graph) == {("src/m:Layout", "src/m:Leaf")}
    assert ("src/m:Page", "src/m:Leaf") in creators(graph)
    assert renderers(graph) == set()
    assert any(u.reason.startswith(JSX_NO_RENDERER) and "not React's runtime" in u.reason for u in graph.unresolved)


@pytest.mark.parametrize(
    "options",
    ['"jsxFactory": "h"', '"jsxImportSource": "preact"', '"jsxFragmentFactory": "Frag"'],
)
def test_a_tsconfig_that_names_another_factory_or_import_source_has_creator_edges_only(make_repo, options):
    graph = index(
        make_repo,
        {"tsconfig.json": '{ "compilerOptions": { "jsx": "react-jsx", %s } }' % options, "src/m.tsx": NON_REACT_FILE},
    )
    assert ("src/m:Page", "src/m:Leaf") in creators(graph)
    assert renderers(graph) == set()
    assert any("tsconfig.json sets" in u.reason for u in graph.unresolved)


@pytest.mark.parametrize(
    "header",
    ["", "/** @jsx React.createElement */", "/** @jsxImportSource react */", "/** @jsxRuntime automatic */", "/** @jsxFrag React.Fragment */"],
)
def test_a_file_that_uses_reacts_runtime_keeps_its_renderer_edges(make_repo, header):
    graph = index(make_repo, {"tsconfig.json": '{ "compilerOptions": { "jsx": "react-jsx", "jsxImportSource": "react" } }', "src/m.tsx": header + NON_REACT_FILE})
    assert renderer_to(graph, "Leaf") == {("src/m:Layout", "src/m:Leaf")}
    assert ("src/m:Page", "src/m:Layout") in renderers(graph)


@pytest.mark.parametrize(
    "imports, base",
    [
        ("import React from 'react';", "React.Component"),
        ("import React from 'react';", "React.PureComponent"),
        ("import * as React from 'react';", "React.Component"),
        ("import { Component } from 'react';", "Component"),
        ("import { PureComponent as Pure } from 'react';", "Pure"),
        ("import { Component as C } from 'react';", "C"),
    ],
)
def test_every_way_of_importing_react_gives_a_class_component(make_repo, imports, base):
    graph = index(
        make_repo,
        {
            "src/m.tsx": imports
            + f"""
            export function Leaf() {{ return <i />; }}
            export class Box extends {base}<any> {{ render() {{ return <Leaf />; }} }}
            export default function App() {{ return <Box />; }}
            """
        },
    )
    assert renderer_to(graph, "Leaf") == {("src/m:Box", "src/m:Leaf")}


def test_ripple_index_counts_jsx_edges_apart_from_calls_and_only_when_there_are_some(make_repo, capsys):
    from ripple import cli

    repo = make_repo(
        {
            "src/m.tsx": "export function util() { return 1; }\nexport function Leaf() { return <i />; }\n"
            "export function Row() { util(); return <li><Leaf /></li>; }\nexport function Page() { return <Row />; }\n"
        }
    )
    assert cli.main(["index", str(repo)]) == 0
    first = capsys.readouterr().out.splitlines()[1]
    assert first.startswith("1 resolved calls, 3 JSX edges, "), first  # util(); Row renders and renders-in Leaf, Page renders Row


def test_ripple_index_says_nothing_of_jsx_in_a_repo_with_none(make_repo, capsys):
    from ripple import cli

    repo = make_repo({"src/m.ts": "export function a() { return b(); }\nexport function b() { return 1; }\n"})
    assert cli.main(["index", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "JSX" not in out and out.splitlines()[1].startswith("1 resolved calls, 0 unresolved calls")
