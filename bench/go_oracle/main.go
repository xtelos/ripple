// The differential oracle for ripple's Go front end: what the Go compiler's own type checker says each call site calls.
// Development time only (generate.py runs it by hand, per GOOS/GOARCH, and ripple's tests read what it wrote and never
// need Go). Usage: oracle -dir DIR [-tags a,b] [pattern...]; GOOS, GOARCH and CGO_ENABLED come from the environment.
//
// Every call expression is classified by golang.org/x/tools/go/types/typeutil.Callee:
//
//	C variant kind caller callee calleefile file:line:col line:col
//	F variant file
//
// (the last field of C is the line and column of the call's closing parenthesis, a position of its own for every call)
//
// kind is E (a static call of a function or a method of a concrete type, callee read from source), X (a static call
// whose callee has no source here), I (a call of an interface method) or D (a dynamic call, a function value: callee
// and calleefile are "-"). A conversion or a builtin is not a call. A method declared on an alias receiver
// (`func (A) M()` with `type A = B`) is named A.M, which is how ripple names it. variant is "prod" for a package as
// `go build` compiles it and "test" for one as `go test` does (internal and external test packages, and what is rebuilt
// for them). F lists every file a variant compiles, which is the only way to know a file is built on this platform.
// A package that does not type-check is an error: a probe is legal Go.
package main

import (
	"flag"
	"fmt"
	"go/ast"
	"go/types"
	"os"
	"sort"
	"strings"

	"golang.org/x/tools/go/packages"
	"golang.org/x/tools/go/types/typeutil"
)

func recvName(sig *types.Signature) (string, bool) {
	r := sig.Recv()
	if r == nil {
		return "", false
	}
	t := r.Type()
	for {
		if p, ok := t.(*types.Pointer); ok {
			t = p.Elem()
			continue
		}
		break
	}
	switch n := t.(type) {
	case *types.Named:
		return n.Obj().Name(), types.IsInterface(n)
	case *types.Alias:
		return n.Obj().Name(), false
	}
	return "", types.IsInterface(t)
}

func funcID(f *types.Func) (string, bool) {
	f = f.Origin()
	pkg := f.Pkg()
	if pkg == nil {
		return "", false
	}
	path := pkg.Path()
	sig := f.Type().(*types.Signature)
	if name, iface := recvName(sig); sig.Recv() != nil {
		if name == "" {
			return path + ":?." + f.Name(), iface
		}
		return path + ":" + name + "." + f.Name(), iface
	}
	return path + ":" + f.Name(), false
}

// receiverOf is the method's receiver type name as written in its declaration, which is the id ripple gives it: a
// method declared on an alias is the alias's.
func receiverOf(d *ast.FuncDecl) string {
	t := d.Recv.List[0].Type
	for {
		switch x := t.(type) {
		case *ast.StarExpr:
			t = x.X
			continue
		case *ast.ParenExpr:
			t = x.X
			continue
		case *ast.IndexExpr:
			t = x.X
			continue
		case *ast.IndexListExpr:
			t = x.X
			continue
		}
		break
	}
	if id, ok := t.(*ast.Ident); ok {
		return id.Name
	}
	return ""
}

func main() {
	dir := flag.String("dir", ".", "directory")
	tags := flag.String("tags", "", "build tags")
	flag.Parse()
	patterns := flag.Args()
	if len(patterns) == 0 {
		patterns = []string{"./..."}
	}
	cfg := &packages.Config{
		Mode: packages.NeedName | packages.NeedFiles | packages.NeedCompiledGoFiles | packages.NeedSyntax | packages.NeedTypes |
			packages.NeedTypesInfo | packages.NeedImports | packages.NeedDeps | packages.NeedModule,
		Tests: true,
		Dir:   *dir,
	}
	if *tags != "" {
		cfg.BuildFlags = []string{"-tags=" + *tags}
	}
	pkgs, err := packages.Load(cfg, patterns...)
	if err != nil {
		fmt.Fprintln(os.Stderr, "load:", err)
		os.Exit(1)
	}
	bad := 0
	for _, p := range pkgs {
		for _, e := range p.Errors {
			fmt.Fprintln(os.Stderr, "package error:", p.ID, e)
			bad++
		}
	}
	if bad > 0 {
		os.Exit(2)
	}
	seen := map[string]bool{}
	var out []string
	add := func(line string) {
		if !seen[line] {
			seen[line] = true
			out = append(out, line)
		}
	}
	for _, p := range pkgs {
		if p.TypesInfo == nil {
			continue
		}
		variant := "prod"
		if strings.Contains(p.ID, " [") {
			variant = "test"
		}
		pkgID := strings.TrimSuffix(p.PkgPath, ".test")
		for _, file := range p.Syntax {
			filename := p.Fset.Position(file.Pos()).Filename
			if strings.Contains(filename, "/go-build/") || strings.HasSuffix(filename, "_testmain.go") {
				continue
			}
			add(fmt.Sprintf("F\t%s\t%s", variant, filename))
			for _, decl := range file.Decls {
				caller := pkgID
				if d, ok := decl.(*ast.FuncDecl); ok {
					caller = pkgID + ":" + d.Name.Name
					if d.Recv != nil && len(d.Recv.List) > 0 {
						if name := receiverOf(d); name != "" {
							caller = pkgID + ":" + name + "." + d.Name.Name
						}
					}
				}
				ast.Inspect(decl, func(n ast.Node) bool {
					call, ok := n.(*ast.CallExpr)
					if !ok {
						return true
					}
					pos := p.Fset.Position(call.Pos())
					// The closing parenthesis is the one position no two calls share (a chain `a.F().G()` starts
					// both at a), so it is what tells two calls of one line apart.
					rparen := p.Fset.Position(call.Rparen)
					where := fmt.Sprintf("%s:%d:%d\t%d:%d", pos.Filename, pos.Line, pos.Column, rparen.Line, rparen.Column)
					if tv, ok := p.TypesInfo.Types[call.Fun]; ok && (tv.IsType() || tv.IsBuiltin()) {
						return true // a conversion or a builtin
					}
					fn, isFunc := typeutil.Callee(p.TypesInfo, call).(*types.Func)
					if !isFunc {
						add(fmt.Sprintf("C\t%s\tD\t%s\t-\t-\t%s", variant, caller, where))
						return true
					}
					id, iface := funcID(fn)
					if id == "" {
						return true
					}
					declared := p.Fset.Position(fn.Origin().Pos()).Filename
					kind := "X"
					switch {
					case iface:
						kind = "I"
					case declared != "":
						kind = "E"
					}
					add(fmt.Sprintf("C\t%s\t%s\t%s\t%s\t%s\t%s", variant, kind, caller, id, declared, where))
					return true
				})
			}
		}
	}
	sort.Strings(out)
	for _, l := range out {
		fmt.Println(l)
	}
}
