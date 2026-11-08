"""Decide the JSX edges: which function creates each component element, and which component renders it.

Two kinds of edge come out of a JsxElement (jsx_facts.py), and they answer different questions:

  renders   the CREATOR. The function whose body writes `<Child />`: who is running when the element is made. The tag
            is read like a call's callee (the same scopes, imports and barrels), so a name that something nearer
            declares hides an import, and a tag that is a variable of unknown value is unresolved. A function component
            and a class component are edges to the function or the class (never to a constructor). `memo(f)` and
            `forwardRef(f)` are edges to f when f is a function ripple can see, `lazy(...)` and anything from outside the
            repo are not edges: the first is unresolved, the second is counted as code outside the repo.
  renderer  the component React was RENDERING when it ran Child's function: the nearest component above Child in React's
            tree. It is an edge only when the source proves it, and otherwise the element is unresolved. Proven:
              (a) the element is in what component C returns, through host elements, fragments, conditionals, `&&`,
                  `||`, `??` and the callback of a `.map` on an array: C is the renderer, and also the creator.
              (b) the element is a child of `<P>...</P>` and P is a function component that places its `children` prop
                  (`children`, `props.children`, `{ children: kids }`) in what it returns the same way: P is the renderer.
                  If P hands `children` to another component Q that places them, Q is.
              (c) the element is what a function written as a prop of `<P render={() => <Child />}>` returns, and P calls
                  that prop where it places the result (`{render(row)}`, `props.render(row)`): P is the renderer.
              A class component's `render` method is C in (a): the renderer is the class React instantiates, that
              is each class an element names (`<Fancy />`) that runs this `render`, even when it inherits it from a base
              class. The base that only defines `render` is never the renderer, and a `render` that anything in the repo
              calls by hand (`this.render()`, `super.render()`, `card.render()`) is not proven.
            Not proven, so no edge: the element passed to a function (`mount(<Home />)`), assigned, stored, returned by a
            helper, shown by an event handler or an effect, children given to a tag from outside the repo (a styled
            wrapper), a context, a component that does not place the prop, a `.map` whose receiver is not known to be an
            array, and the children of an element that is itself an argument of cloneElement, createElement or
            Children.map and the like (they may replace those children: the creator edges stay).
            A prop that the component assigns anywhere in its body, whatever the order (`children = x`, `props = {...}`,
            `props.children = x`, `delete props.x`, `Object.assign(props, x)`, in a loop or a function declared below), is
            not what the caller passed, so nothing the component does with it is proven.
            All of this is React's order of work (a component runs when React renders it, not when its element is made),
            so a file whose JSX is compiled by another runtime (a `@jsx h` or `@jsxImportSource` pragma that is not
            React's, or a tsconfig `jsxFactory`, `jsxFragmentFactory` or `jsxImportSource` that is not) has creator edges
            only. A bundler's own setting outside tsconfig is not read.

A function is a COMPONENT, so that what it returns is rendered inside it, when some element in the repo has it for a tag
and nothing in the repo calls it as a function or hands it on by name as a prop (`render={Home}`): `Dual()` and `<Dual />`
leave it unknown which one ran. React's Fragment, StrictMode, Suspense and Profiler render their children where they stand,
as a host element does. A file the parser had to recover from places nothing. A class is one
when it extends React's Component or PureComponent. A component that is only handed to code outside the repo, or called
there as a function, is not seen.
"""

from __future__ import annotations

from .jsx_facts import JSX_NO_RENDERER, JSX_TAG_UNRESOLVED, Placement
from .model import Edge, Unresolved
from .resolver import EXT_BUILTIN_VALUE, Resolver, Target, unknown

EXT_COMPONENT = "a JSX component from outside the repo"

LAZY = "a lazy component is loaded when it first renders, so ripple cannot see which one"
NOT_A_FUNCTION = "what the wrapper is given is not a function ripple can see"
NOT_A_COMPONENT = "it is not a function or a class"


class JsxEdges:
    """The JSX edges of one resolver's scopes: call run() after the ordinary edges are known."""

    def __init__(self, resolver: Resolver, edges: list[Edge]):
        self.r = resolver
        self.called = {e.callee for e in edges}  # whatever is called, constructed or passed by name to a builtin
        self.found: list[Edge] = []
        self.unresolved: list[Unresolved] = []
        self.external = 0
        self.targets: dict[int, str] = {}  # id(JsxElement) -> the id of the component it names
        self.rendered: set[str] = set()  # every component some element names
        self._owners: dict[str, frozenset[str]] = {}

    # -- the creator -----------------------------------------------------------------------

    def hand_on(self) -> None:
        """A function handed on by name as a prop (`render={Home}`) may be called as a function by what receives it."""
        for scope in self.r.scopes.values():
            for chain in getattr(scope, "handed", ()):
                try:
                    target = self.r.resolve_chain(scope, chain)
                except RecursionError:
                    continue
                if target.kind == "symbol":
                    self.called.add(target.value)

    def run(self) -> tuple[list[Edge], list[Unresolved], int]:
        self.hand_on()
        elements = []
        for scope in self.r.scopes.values():
            for element in getattr(scope, "jsx", ()):
                target = self.tag(scope, element)
                if target is not None:
                    self.targets[id(element)] = target
                    self.rendered.add(target)
                    elements.append((scope, element, target))
                    self.found.append(Edge(scope.id, target, scope.file, element.line, "renders"))
        for scope, element, target in elements:
            owners, why = self.owners(scope, element.placement, ())
            for owner in sorted(owners):
                self.found.append(Edge(owner, target, scope.file, element.line, "renderer"))
            if not owners:
                self.unresolved.append(Unresolved(scope.id, scope.file, element.line, element.text, JSX_NO_RENDERER + why))
        return self.found, self.unresolved, self.external

    def tag(self, scope, element) -> str | None:
        """The component an element names: its id, else None (counted outside the repo, or unresolved with the reason)."""
        if element.chain is None:
            self.fail(scope, element, element.why)
            return None
        r = self.r
        r._tag_mode = True
        try:
            target = r.resolve_chain(scope, element.chain)
        except RecursionError:
            target = unknown("too deeply nested to follow")
        finally:
            r._tag_mode = False
        if target.kind == "symbol":
            if r.scopes[target.value].kind in ("function", "class"):
                return target.value
            self.fail(scope, element, f"{target.value} is a {r.scopes[target.value].kind}, {NOT_A_COMPONENT}")
        elif target.kind == "external":
            self.external += 1
            r.external_reasons[EXT_COMPONENT] += 1
        elif target.kind == "unknown":
            self.fail(scope, element, target.reason)
        else:
            self.fail(scope, element, f"it is {target.kind if target.kind != 'each' else 'one of several'}, {NOT_A_COMPONENT}")
        return None

    def fail(self, scope, element, why: str) -> None:
        why = why.replace("the receiver is", "the tag is", 1) if why.startswith("the receiver is") else why
        self.unresolved.append(Unresolved(scope.id, scope.file, element.line, element.text, JSX_TAG_UNRESOLVED + why))

    # -- the renderer ----------------------------------------------------------------------

    def owner(self, scope_id: str) -> frozenset[str]:
        """The components whose tree the return of this function is part of: none when it is not known to be rendered."""
        if scope_id not in self._owners:
            self._owners[scope_id] = frozenset(self._owner(scope_id))
        return self._owners[scope_id]

    def _owner(self, scope_id: str) -> set[str]:
        """A function component is its own owner. A `render` method has as owners the classes React instantiates that
        run it: each class some element names whose `render` this is (a subclass that inherits it is the owner, never the
        base that only defines it), when nothing in the repo calls `render` by hand."""
        r = self.r
        scope = r.scopes[scope_id]
        if scope.kind == "function":
            return {scope_id} if scope_id in self.rendered and scope_id not in self.called else set()
        if scope.kind == "method" and scope_id.rsplit(".", 1)[-1].split("#")[0] == "render" and scope.parent in r.scopes:
            if scope_id in self.called:
                return set()
            return {
                cls
                for cls in self.rendered
                if r.scopes[cls].kind == "class"
                and r.unresolved_base(cls) is None
                and r.class_member(cls, "render") == scope_id
                and self.is_component_class(cls)
            }
        return set()

    def is_component_class(self, cls: str) -> bool:
        r = self.r
        for klass in r.mro(cls):
            r.base_classes(klass)
            # `import React from 'react'` makes the base pkg:react.default.Component: the default export is the namespace
            if any(base.replace(".default.", ".") in r.profile.component_bases for base in r._external_bases.get(klass, [])):
                return True
        return False

    def discarded(self, element, seen: tuple = ()) -> str:
        """The React function (cloneElement, Children.map...) an element, or an element it is inside, is passed to, when it
        may replace the element's children: what such an element holds is then not known to be what renders."""
        placement = element.placement
        if placement.call in self.r.profile.child_replacers:
            return placement.call
        if placement.kind in ("child", "attr") and placement.element is not None and id(element) not in seen:
            return self.discarded(placement.element, (*seen, id(element)))
        return ""

    def runs_now(self, scope, host) -> bool:
        """Whether a `.map(...)` call runs its callback before it returns: it does on an array, which is all ripple will say."""
        try:
            found = self.r.resolve_call(scope, host)
        except RecursionError:
            return False
        return found.kind == "external" and found.reason == EXT_BUILTIN_VALUE

    def owners(self, scope, placement: Placement, stack: tuple) -> tuple[set[str], str]:
        """(the components that render a value with this placement, why none can be proven when the set is empty).

        `scope` is the scope the value was written in, where the `.map` calls the placement went through are read."""
        r = self.r
        if placement.runtime:
            return set(), placement.runtime
        for host in placement.hosts:
            if not self.runs_now(scope, host):
                return set(), f"`{host.text}` is not known to run its callback at once (its receiver is not known to be an array)"
        if placement.kind == "none":
            return set(), placement.why
        if placement.kind == "scope":
            found = self.owner(placement.scope)
            if found:
                return set(found), ""
            return set(), f"{placement.scope} is not known to be rendered as a component, or is also called as a function"
        target = self.targets.get(id(placement.element))
        if target is None:
            return set(), f"<{placement.element.text}> is not a component of the repo"
        holder = r.scopes[target]
        if holder.kind != "function" or not self.owner(target):
            return set(), f"{target} is not a function component that ripple reads and knows is rendered as one"
        replacer = self.discarded(placement.element)
        if replacer:
            return set(), f"<{placement.element.text}> is passed to {replacer.split('.', 1)[-1]}, which may replace what it holds"
        key = "children" if placement.kind == "child" else placement.key
        slots = getattr(holder, "prop_calls" if placement.called else "slots", {}).get(key, [])
        if not slots:
            what = "calls" if placement.called else "places"
            return set(), f"{target} does not {what} its `{key}` where it returns it"
        if (target, key) in stack:
            return set(), f"{target} passes its `{key}` round in a cycle"
        found: set[str] = set()
        for slot in slots:
            more, _ = self.owners(holder, slot, (*stack, (target, key)))
            found |= more
        if found:
            return found, ""
        return set(), f"{target} places its `{key}` where no component is proven to render it"
