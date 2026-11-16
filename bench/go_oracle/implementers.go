// The implementers half of the oracle (-implementers): for every call of an interface method, the methods of the repo that
// call can dispatch to, by the type checker's own answer to "does this type implement this interface".
//
//	M variant universe interface method candidates file:line:col line:col
//	P variant universe packages
//
// (a P row follows each M row: the import paths, comma separated, of the repo packages the universe has, which is how a
// candidate of a package that is not in a universe is told from a counterexample: see generate.py agreed_candidates)
//
// interface is the import path, a colon and the name of the interface the value is held as (`-` for an interface literal),
// method the called method, and candidates a comma separated list of `id@file`, each a method declared inside the repo
// (-repo) that a type implementing the interface has under that name: for every named type T of the repo that is not
// generic and not an interface, when *T implements the interface (its method set is the largest: a value of T or of *T is
// what the interface can hold), the method types.LookupFieldOrMethod finds on *T for the name (promoted through embedded
// fields as the spec's depth rule says). A method that is the interface's own (T embeds an interface) has no source and
// is left out. universe says which set of packages the types were checked in: a test binary recompiles the packages that
// depend on the package under test, so a type of one build is never identical to a type of another. A site seen in
// several universes gets one row each, and the generator keeps the candidates every one of them has. Only a type declared
// at package level is a candidate (a type declared inside a function can implement an interface too, which ripple does not
// read as one: that is recall it does not claim).
//
// A universe is one BUILD UNIT, never a union of programs. A program links exactly its transitive import closure, so the
// universe of a call written in a package main is that program's closure (key "main:" and the import path), and a type of
// another program is no candidate. A test binary links the package under test with its _test.go files, its external test
// package and what those import, and never another package's _test.go files (the key is the test main's ID). A call in
// any other package (a library) is in every program and every test binary that links its package, and in a program of
// another repository that imports it, so its universe (key "lib:" and the import path) is the packages some unit of the
// repo links together with the call's package, and every package neither of them is a main package or lies below an
// internal directory (a program outside the repo can import both), provided one module of the repo builds against both
// (the go tool's own list of the modules this run builds against, which is how the run loads them). Every set holds the
// repo's packages as ONE type-checked instance each, so identity of types is the compiler's: a plain package is type-checked
// once, whichever unit links it. A package that a test binary recompiles (it imports the package under test) is its own
// universe's, the test binary's.
package main

import (
	"fmt"
	"go/ast"
	"go/types"
	"sort"
	"strings"

	"golang.org/x/tools/go/packages"
)

type fit struct {
	iface *types.Interface
	named *types.Named
}

type universes struct {
	repo   string
	roots  []*packages.Package
	set    map[string]map[string]*packages.Package // universe key -> package path -> package
	named  map[string][]*types.Named
	answer map[string]string
	fits   map[fit]bool
	plain  map[string]*packages.Package // every package as `go build` compiles it, by import path
	units  []map[string]*packages.Package
	built  bool
}

func suffixOf(p *packages.Package) string {
	if i := strings.Index(p.ID, " ["); i >= 0 {
		return p.ID[i+2 : len(p.ID)-1]
	}
	return ""
}

func isTestMain(p *packages.Package) bool {
	return suffixOf(p) == "" && strings.HasSuffix(p.PkgPath, ".test")
}

func internalPath(path string) bool {
	return strings.Contains("/"+path+"/", "/internal/")
}

func newUniverses(repo string, roots []*packages.Package) *universes {
	return &universes{repo: repo, roots: roots, set: map[string]map[string]*packages.Package{}, named: map[string][]*types.Named{}, answer: map[string]string{}, fits: map[fit]bool{}}
}

// keyOf is the universe a call is compiled in: the test binary's for a package a test binary compiles, the program's for a
// package main, the library universe for any other package.
func (u *universes) keyOf(p *packages.Package) string {
	switch {
	case suffixOf(p) != "":
		return suffixOf(p)
	case p.Name == "main":
		return "main:" + p.PkgPath
	}
	return "lib:" + p.PkgPath
}

// buildUnits lists every build unit the load has: each program (the closure of a package main) and each test binary (the
// closure of a test main, which holds the recompiled packages), and the plain packages.
func (u *universes) buildUnits() {
	if u.built {
		return
	}
	u.built = true
	u.plain = map[string]*packages.Package{}
	seenPlain := map[*packages.Package]bool{}
	for _, p := range u.roots {
		switch {
		case isTestMain(p):
			into := map[string]*packages.Package{}
			closure(p, into, map[*packages.Package]bool{}, true)
			u.units = append(u.units, into)
		case suffixOf(p) == "":
			closure(p, u.plain, seenPlain, false)
			if p.Name == "main" {
				into := map[string]*packages.Package{}
				closure(p, into, map[*packages.Package]bool{}, false)
				u.units = append(u.units, into)
			}
		}
	}
}

// libraryOf is the universe of a call in a library package: see the comment at the top.
func (u *universes) libraryOf(path string) map[string]*packages.Package {
	u.buildUnits()
	home := u.plain[path]
	into := map[string]*packages.Package{}
	if home == nil {
		return into
	}
	into[path] = home
	for _, unit := range u.units {
		if unit[path] == nil {
			continue
		}
		for other := range unit {
			if pkg := u.plain[other]; pkg != nil {
				into[other] = pkg
			}
		}
	}
	if home.Name != "main" && !internalPath(path) {
		for other, pkg := range u.plain {
			if pkg.Name != "main" && !internalPath(other) {
				into[other] = pkg
			}
		}
	}
	return into
}

func closure(p *packages.Package, into map[string]*packages.Package, seen map[*packages.Package]bool, override bool) {
	if seen[p] {
		return
	}
	seen[p] = true
	if _, have := into[p.PkgPath]; override || !have {
		into[p.PkgPath] = p
	}
	for _, q := range p.Imports {
		closure(q, into, seen, override)
	}
}

// packagesOf is the packages of one universe (see keyOf). The universe of a test binary (key: the ID of its test main) is
// the packages that binary links: the closure of the test main, which has the package under test with its test files, its
// external test package, and what those import (a package that imports the package under test is recompiled against
// the test version, and is there only if the tests import it). The universe of a program (key "main:" and the path of its
// package main) is the closure of that package alone. The universe of a library package is libraryOf.
func (u *universes) packagesOf(key string) map[string]*packages.Package {
	if found, ok := u.set[key]; ok {
		return found
	}
	into := map[string]*packages.Package{}
	switch {
	case strings.HasPrefix(key, "main:"):
		seen := map[*packages.Package]bool{}
		for _, p := range u.roots {
			if suffixOf(p) == "" && p.Name == "main" && !isTestMain(p) && p.PkgPath == strings.TrimPrefix(key, "main:") {
				closure(p, into, seen, false)
			}
		}
	case strings.HasPrefix(key, "lib:"):
		into = u.libraryOf(strings.TrimPrefix(key, "lib:"))
	default:
		seen := map[*packages.Package]bool{}
		for _, p := range u.roots {
			if p.ID == key {
				closure(p, into, seen, true)
			}
		}
	}
	u.set[key] = into
	return into
}

// present is the row that says which packages one universe has, by path: a type of a package that a universe has and that
// does not implement the interface there is a counterexample, a type of a package it does not have is no evidence.
func (u *universes) present(variant, suffix string) string {
	var paths []string
	for path, p := range u.packagesOf(suffix) {
		if len(p.CompiledGoFiles) > 0 && u.inRepo(p.CompiledGoFiles[0]) {
			paths = append(paths, path)
		}
	}
	sort.Strings(paths)
	return fmt.Sprintf("P\t%s\t%s\t%s", variant, suffix, strings.Join(paths, ","))
}

func (u *universes) inRepo(filename string) bool {
	return filename != "" && strings.HasPrefix(filename, u.repo+"/") && !strings.Contains(filename[len(u.repo):], "/vendor/")
}

func (u *universes) candidates(suffix string) []*types.Named {
	if found, ok := u.named[suffix]; ok {
		return found
	}
	var found []*types.Named
	paths := []string{}
	in := u.packagesOf(suffix)
	for path := range in {
		paths = append(paths, path)
	}
	sort.Strings(paths)
	for _, path := range paths {
		p := in[path]
		if p.Types == nil || len(p.CompiledGoFiles) == 0 || !u.inRepo(p.CompiledGoFiles[0]) {
			continue
		}
		scope := p.Types.Scope()
		for _, name := range scope.Names() {
			tn, ok := scope.Lookup(name).(*types.TypeName)
			if !ok || tn.IsAlias() {
				continue
			}
			n, ok := tn.Type().(*types.Named)
			if !ok || n.TypeParams().Len() > 0 || types.IsInterface(n) {
				continue
			}
			found = append(found, n)
		}
	}
	u.named[suffix] = found
	return found
}

// implements asks the type checker once per (interface, type): the universes of programs share the plain packages' types.
func (u *universes) implements(ptr *types.Pointer, n *types.Named, iface *types.Interface) bool {
	key := fit{iface, n}
	if got, ok := u.fits[key]; ok {
		return got
	}
	got := types.Implements(ptr, iface)
	u.fits[key] = got
	return got
}

func deref(t types.Type) types.Type {
	for {
		t = types.Unalias(t)
		if p, ok := t.(*types.Pointer); ok {
			t = p.Elem()
			continue
		}
		return t
	}
}

// heldAs is the interface type the called method is looked up on: the static type of the operand, or when the method is
// promoted through embedded fields, the type of the last embedded field (an interface the struct holds).
func heldAs(p *packages.Package, call *ast.CallExpr) (types.Type, *types.Func) {
	sel, ok := ast.Unparen(call.Fun).(*ast.SelectorExpr)
	if !ok {
		return nil, nil
	}
	s := p.TypesInfo.Selections[sel]
	if s == nil || s.Kind() != types.MethodVal {
		return nil, nil
	}
	fn, ok := s.Obj().(*types.Func)
	if !ok {
		return nil, nil
	}
	t := s.Recv()
	path := s.Index()
	for _, i := range path[:len(path)-1] {
		st, ok := deref(t).Underlying().(*types.Struct)
		if !ok {
			return nil, nil
		}
		t = st.Field(i).Type()
	}
	t = types.Unalias(t)
	if _, isParam := t.(*types.TypeParam); isParam {
		return nil, nil
	}
	if !types.IsInterface(t) {
		return nil, nil
	}
	return t, fn
}

func interfaceKey(t types.Type) string {
	if n, ok := t.(*types.Named); ok && n.Obj().Pkg() != nil {
		return n.Obj().Pkg().Path() + ":" + n.Obj().Name()
	}
	return "-"
}

// implementers is the row of one interface call: the candidates in the universe of the package the call is written in.
func (u *universes) implementers(p *packages.Package, call *ast.CallExpr, variant, where string) string {
	t, called := heldAs(p, call)
	if t == nil {
		return ""
	}
	iface, ok := t.Underlying().(*types.Interface)
	if !ok || !iface.IsMethodSet() {
		return ""
	}
	suffix := u.keyOf(p)
	key := fmt.Sprintf("%s|%p|%s|%s", suffix, t, called.Pkg().Path(), called.Name())
	cands, ok := u.answer[key]
	if !ok {
		var found []string
		for _, n := range u.candidates(suffix) {
			ptr := types.NewPointer(n)
			if !u.implements(ptr, n, iface) {
				continue
			}
			obj, _, _ := types.LookupFieldOrMethod(ptr, true, called.Pkg(), called.Name())
			fn, isFunc := obj.(*types.Func)
			if !isFunc {
				continue
			}
			if recv := fn.Type().(*types.Signature).Recv(); recv != nil && types.IsInterface(recv.Type()) {
				continue
			}
			id, _ := funcID(fn)
			declared := p.Fset.Position(fn.Origin().Pos()).Filename
			if id == "" || !u.inRepo(declared) {
				continue
			}
			found = append(found, id+"@"+declared)
		}
		sort.Strings(found)
		out := found[:0]
		for i, f := range found {
			if i == 0 || f != found[i-1] {
				out = append(out, f)
			}
		}
		cands = strings.Join(out, ",")
		u.answer[key] = cands
	}
	return fmt.Sprintf("M\t%s\t%s\t%s\t%s\t%s\t%s", variant, suffix, interfaceKey(t), called.Name(), cands, where)
}
