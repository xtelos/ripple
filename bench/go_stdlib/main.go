// What the Go standard library says each exported name is, for one GOOS/GOARCH (generate.py merges the platforms and writes
// ripple/go_stdlib.py). Development time only: ripple reads the table and never needs Go.
//
// ripple does not read the source of a package outside the repo, so `pkg.F(x)` and `v.F(x)` for an outside package or
// type could be a function or method, or a variable or struct field that holds a function (and so may hold repo code).
// For the standard library the source is on disk, so this program type-checks every package from it (go/importer, "source")
// and prints, as JSON, per package:
//
//	funcs  the exported names that are func declarations
//	other  the exported names that are anything else (a variable, a constant, a type): generate.py needs them to drop a
//	       name that is a func on one platform and not on another
//	types  per exported type: kind (i interface, s struct, o other) and, over the method set of *T (promotion through
//	       embedded fields included), the exported names that are methods and the exported names that are fields of a
//	       function type. A name that lookup finds ambiguous or shadowed is in neither list.
//
// Usage: GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go run . > linux-amd64.json
package main

import (
	"encoding/json"
	"fmt"
	"go/ast"
	"go/importer"
	"go/token"
	"go/types"
	"os"
	"os/exec"
	"sort"
	"strings"
)

type typeInfo struct {
	Kind    string   `json:"k"`
	Methods []string `json:"m"`
	Fields  []string `json:"f"`
}

type pkgInfo struct {
	Funcs []string            `json:"funcs"`
	Other []string            `json:"other"`
	Types map[string]typeInfo `json:"types"`
}

func isFunc(t types.Type) bool {
	_, ok := t.Underlying().(*types.Signature)
	return ok
}

// fieldNames collects the exported names of struct fields reachable through embedded structs (a candidate list: lookup decides).
func fieldNames(t types.Type, seen map[types.Type]bool, out map[string]bool) {
	t = types.Unalias(t)
	if p, ok := t.(*types.Pointer); ok {
		t = types.Unalias(p.Elem())
	}
	if seen[t] {
		return
	}
	seen[t] = true
	st, ok := t.Underlying().(*types.Struct)
	if !ok {
		return
	}
	for i := 0; i < st.NumFields(); i++ {
		f := st.Field(i)
		if f.Exported() {
			out[f.Name()] = true
		}
		if f.Embedded() {
			fieldNames(f.Type(), seen, out)
		}
	}
}

// throughInterface reports whether the selection path (the embedded fields, then the method) from t passes an interface.
func throughInterface(t types.Type, path []int) bool {
	cur := types.Unalias(t)
	for _, i := range path[:len(path)-1] {
		if p, ok := cur.(*types.Pointer); ok {
			cur = types.Unalias(p.Elem())
		}
		st, ok := cur.Underlying().(*types.Struct)
		if !ok {
			return false
		}
		cur = types.Unalias(st.Field(i).Type())
		if p, ok := cur.(*types.Pointer); ok {
			cur = types.Unalias(p.Elem())
		}
		if _, ok := cur.Underlying().(*types.Interface); ok {
			return true
		}
	}
	return false
}

func describe(pkg *types.Package, t types.Type) typeInfo {
	info := typeInfo{Kind: "o", Methods: []string{}, Fields: []string{}}
	switch t.Underlying().(type) {
	case *types.Interface:
		info.Kind = "i"
	case *types.Struct:
		info.Kind = "s"
	}
	candidates := map[string]bool{}
	var set *types.MethodSet
	if info.Kind == "i" {
		set = types.NewMethodSet(t)
	} else {
		set = types.NewMethodSet(types.NewPointer(t))
	}
	for i := 0; i < set.Len(); i++ {
		if n := set.At(i).Obj().Name(); ast.IsExported(n) {
			candidates[n] = true
		}
	}
	fieldNames(t, map[types.Type]bool{}, candidates)
	for name := range candidates {
		recv := t
		if info.Kind != "i" {
			recv = types.NewPointer(t)
		}
		obj, path, _ := types.LookupFieldOrMethod(recv, true, pkg, name)
		switch o := obj.(type) {
		case *types.Func:
			if info.Kind != "i" && throughInterface(t, path) {
				continue // promoted from an embedded interface: the method that runs is the dynamic value's, so it is not proven the library's
			}
			info.Methods = append(info.Methods, name)
		case *types.Var:
			if o.IsField() && isFunc(o.Type()) {
				info.Fields = append(info.Fields, name)
			}
		}
	}
	sort.Strings(info.Methods)
	sort.Strings(info.Fields)
	return info
}

func main() {
	out, err := exec.Command("go", "list", "std").Output()
	if err != nil {
		fmt.Fprintln(os.Stderr, "go list std:", err)
		os.Exit(1)
	}
	fset := token.NewFileSet()
	imp := importer.ForCompiler(fset, "source", nil)
	result := map[string]pkgInfo{}
	for _, path := range strings.Fields(string(out)) {
		if path == "unsafe" || strings.HasPrefix(path, "vendor/") || strings.HasPrefix(path, "cmd/") || strings.Contains(path+"/", "/internal/") || strings.HasPrefix(path, "internal/") {
			continue
		}
		pkg, err := imp.Import(path)
		if err != nil {
			fmt.Fprintln(os.Stderr, "skipped", path, err)
			continue
		}
		info := pkgInfo{Funcs: []string{}, Other: []string{}, Types: map[string]typeInfo{}}
		scope := pkg.Scope()
		for _, name := range scope.Names() {
			if !ast.IsExported(name) {
				continue
			}
			switch obj := scope.Lookup(name).(type) {
			case *types.Func:
				info.Funcs = append(info.Funcs, name)
			case *types.TypeName:
				info.Other = append(info.Other, name)
				info.Types[name] = describe(pkg, obj.Type())
			default:
				info.Other = append(info.Other, name)
			}
		}
		result[path] = info
	}
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", " ")
	if err := enc.Encode(result); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
