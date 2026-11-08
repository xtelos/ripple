"""The facts a front end with JSX hands to the resolver, besides the ones in facts.py.

A component element is not a call, so it has no RawCall: the front end writes a JsxElement for each component
element it reads, and says, in a Placement, where the element ends up in what React renders as far as the syntax
shows. It never decides which component that makes the renderer: jsx.py does, once every scope is known.

Scopes of a front end with JSX are JsxScope and JsxModule. They are the scopes of facts.py with the JSX facts added,
and are only ever made by the TypeScript front end, so the facts a Python file produces are the ones they always were.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .facts import ExportingModule, Scope

# The reasons the JSX edges are unresolved with, by their beginning: the rest says why. They are how a reader of the
# unresolved list tells an element whose tag is unknown from one whose renderer is not proven.
JSX_TAG_UNRESOLVED = "the JSX tag is not a component ripple can name: "
JSX_NO_RENDERER = "the component that renders it is not proven: "


@dataclass
class Placement:
    """Where a value written in a component ends up in what React renders, as far as the syntax says.

    The value is a JSX element, `children`, or the result of calling a function-valued prop. Walking out of it through
    only what does not change which component holds it (parentheses, `&&`, `||`, `??`, a conditional, a host element
    such as <div>, a fragment, and the callback of `.map`) leads to one of:
      "scope"  the value is returned by the function `scope`, so it is part of what that function renders
      "child"  the value is a child of the component element `element`
      "attr"   the value is the prop `key` of the component element `element`; `called` when it is the result of a
               function written as that prop (a render prop), not the value itself
      "none"   anywhere else (assigned, passed on, in an event handler, in an effect...): `why` says where
    `hosts` are the `.map(...)` calls the walk went through: each runs its callback at once only if its receiver is an
    array, which only the resolver can say. For "none" at an argument, `call` is the id of the React function the value is
    passed to (`pkg:react.cloneElement`) when it is one, else "". `runtime` is why the file does not use React's JSX
    runtime, when it does not: where a component runs is then not known, and nothing here proves a renderer.
    """

    kind: str = "none"
    scope: str = ""
    element: JsxElement | None = None
    key: str = ""
    called: bool = False
    hosts: list = field(default_factory=list)
    why: str = ""
    call: str = ""
    runtime: str = ""


@dataclass
class JsxElement:
    """A component element written in a function: `<Child />`, `<ns.Child>...</ns.Child>`.

    chain is the tag as a reference the resolver reads like a call's callee (its first name under the key the front end
    bound it by), or None when the tag cannot be read as one (`<this.Child />`), with why. A host tag (<div />, a name
    that starts with a lowercase letter) is never a component and has no JsxElement.
    """

    chain: list | None
    text: str
    line: int
    placement: Placement = field(default_factory=Placement)
    why: str = ""


@dataclass
class JsxFields:
    """What a scope of a language with JSX adds to a Scope.

    jsx are the component elements written in the scope (an anonymous function inside it counts as the scope).
    A function component receives its props as one parameter: slots[key] are the placements of the prop `key` itself
    (`children`, a node passed as a prop) and prop_calls[key] those of a call of the function the prop holds.
    wrapped maps a name to the (wrapper chain, function chain) of `const X = memo(f)`. handed are the references (as
    chains, like a tag) written as the whole value of a prop, `<Route render={Home} />`: a function handed on by name is
    one the code that receives it may call as a function.
    """

    jsx: list = field(default_factory=list)
    handed: list = field(default_factory=list)
    slots: dict = field(default_factory=dict)
    prop_calls: dict = field(default_factory=dict)
    wrapped: dict = field(default_factory=dict)


@dataclass
class JsxScope(JsxFields, Scope):
    pass


@dataclass
class JsxModule(JsxFields, ExportingModule):
    pass
