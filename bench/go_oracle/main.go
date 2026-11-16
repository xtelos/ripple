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
// for them). With -implementers an I call is followed by an M row (the repo methods it may dispatch to) and a P row (the repo
// packages of the universe the call was compiled in), both described in implementers.go. F lists every file a variant compiles,
// which is the only way to know a file is built on this platform.
// A package that does not type-check is an error: a probe is legal Go.
package main

import (
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"go/ast"
	"go/types"
	"os"
	"os/exec"
	"path/filepath"
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

func realPath(path string) string {
	if abs, err := filepath.Abs(path); err == nil {
		path = abs
	}
	if real, err := filepath.EvalSymlinks(path); err == nil {
		path = real
	}
	return path
}

// otherModules is a pattern for the packages of each module of the repo (other than the one in ownDir) that the go tool
// builds this module against: `go list -m all` lists the build list, with a replace or a workspace member at its local
// directory.
func otherModules(cfg *packages.Config, repo, ownDir string) []string {
	repoDir := ownDir
	if repo != "" {
		repoDir = realPath(repo)
	}
	command := exec.Command("go", "list", "-m", "-json", "all")
	command.Dir = cfg.Dir
	command.Env = append(os.Environ(), "GOFLAGS=")
	out, err := command.Output()
	if err != nil {
		return nil
	}
	decoder := json.NewDecoder(bytes.NewReader(out))
	var found []string
	for decoder.More() {
		var m struct{ Path, Dir string }
		if decoder.Decode(&m) != nil {
			break
		}
		if m.Dir == "" {
			continue
		}
		dir := realPath(m.Dir)
		if dir == ownDir || !strings.HasPrefix(dir+"/", repoDir+"/") {
			continue
		}
		found = append(found, m.Path+"/...")
	}
	return found
}

func main() {
	dir := flag.String("dir", ".", "directory")
	tags := flag.String("tags", "", "build tags")
	wantImplementers := flag.Bool("implementers", false, "also write M rows: the repo methods each interface call can dispatch to (implementers.go)")
	repo := flag.String("repo", "", "the directory whose types are candidate implementers (default -dir)")
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
	ownDir := *dir
	if abs, err := filepath.Abs(ownDir); err == nil {
		ownDir = abs
	}
	if real, err := filepath.EvalSymlinks(ownDir); err == nil {
		ownDir = real
	}
	if *wantImplementers {
		// The packages of every other module of the repo that this module builds against (the go tool's own build list: a
		// replace, a go.work) are loaded with it, so that a library call here and a type there are in one run. They are
		// read for their interface calls as implementers only.
		patterns = append(patterns, otherModules(cfg, *repo, ownDir)...)
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
	repoDir := *repo
	if repoDir == "" {
		repoDir = *dir
	}
	if abs, err := filepath.Abs(repoDir); err == nil {
		repoDir = abs
	}
	if real, err := filepath.EvalSymlinks(repoDir); err == nil {
		repoDir = real
	}
	universe := newUniverses(repoDir, pkgs)
	seen := map[string]bool{}
	var out []string
	add := func(line string) {
		if !seen[line] {
			seen[line] = true
			out = append(out, line)
		}
	}
	// With -implementers the packages of the repo that this module only imports (another module of a go.work or a replace)
	// are read for their interface calls too, as implementers only: the call is in a build that has the types of the
	// module that imports it, which this module's own packages are. They give no C or F row.
	roots := map[*packages.Package]bool{}
	scanned := append([]*packages.Package{}, pkgs...)
	for _, p := range pkgs {
		// a package of this module (the other modules' packages were loaded for the implementers, and give no C or F row)
		roots[p] = len(p.CompiledGoFiles) == 0 || strings.HasPrefix(p.CompiledGoFiles[0], ownDir+"/") || !universe.inRepo(p.CompiledGoFiles[0])
	}
	if *wantImplementers {
		visited := map[*packages.Package]bool{}
		for _, p := range pkgs {
			visited[p] = true
		}
		var visit func(p *packages.Package)
		visit = func(p *packages.Package) {
			for _, q := range p.Imports {
				if visited[q] {
					continue
				}
				visited[q] = true
				if !roots[q] && len(q.CompiledGoFiles) > 0 && universe.inRepo(q.CompiledGoFiles[0]) {
					scanned = append(scanned, q)
				}
				visit(q)
			}
		}
		for _, p := range pkgs {
			visit(p)
		}
	}
	for _, p := range scanned {
		if p.TypesInfo == nil {
			continue
		}
		only := !roots[p]
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
			if !only {
				add(fmt.Sprintf("F\t%s\t%s", variant, filename))
			}
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
						if !only {
							add(fmt.Sprintf("C\t%s\tD\t%s\t-\t-\t%s", variant, caller, where))
						}
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
					if !only {
						add(fmt.Sprintf("C\t%s\t%s\t%s\t%s\t%s\t%s", variant, kind, caller, id, declared, where))
					}
					if iface && *wantImplementers {
						if row := universe.implementers(p, call, variant, where); row != "" {
							add(row)
							add(universe.present(variant, universe.keyOf(p)))
						}
					}
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
