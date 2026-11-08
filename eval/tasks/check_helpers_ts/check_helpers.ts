// Shared by the hidden checks of the node tasks, copied next to them (into _eval_check/) at check
// time. The TypeScript counterpart of eval/tasks/check_helpers.py: where the Python helpers walk the
// repo's .py files with `ast`, these walk its .ts/.tsx/.js/.jsx files with the `typescript` compiler
// API, taken from the repo's own node_modules, so the parse is the one the repo's toolchain makes.
//
// Names only, never strings or comments (a property key written as a string counts: it is a name),
// so a docstring that mentions the old name is fine and a leftover call or import is not.

import * as fs from 'fs'
import * as path from 'path'
import * as tsNamespace from 'typescript'
import { execFileSync } from 'child_process'

// CJS interop differs between vitest (the namespace carries .default) and ts-jest without esModuleInterop (it is the module).
const ts: typeof tsNamespace = (tsNamespace as any).default ?? tsNamespace

export const REPO = path.resolve(process.env.EVAL_REPO_ROOT ?? process.cwd())

const EXTENSIONS = /\.(?:[cm]?[jt]sx?)$/
const SKIPPED_DIRS = new Set(['node_modules', 'dist', 'build', 'coverage', 'out', 'tmp', '_eval_check'])

export type Kind = 'attr' | 'name' | 'import' | 'def' | 'param' | 'kwarg'

export interface Reference {
  file: string
  line: number
  kind: Kind
  detail: string
}

function* walk(root: string, dir = ''): Generator<string> {
  const entries = fs.readdirSync(path.join(root, dir), { withFileTypes: true }).sort((a, b) => (a.name < b.name ? -1 : 1))
  for (const entry of entries) {
    const rel = path.posix.join(dir.split(path.sep).join('/'), entry.name)
    if (entry.isSymbolicLink()) {
      if (entry.name.startsWith('.') || SKIPPED_DIRS.has(entry.name)) continue
      // a link is not followed (it could point anywhere), so whatever it hides would go unchecked: fail, never skip
      throw new Error(`${rel} is a symbolic link: the scan does not follow links, so what it points at would go unchecked`)
    }
    if (entry.isDirectory()) {
      if (entry.name.startsWith('.') || SKIPPED_DIRS.has(entry.name)) continue
      yield* walk(root, rel)
    } else if (entry.isFile() && EXTENSIONS.test(entry.name)) {
      yield rel
    }
  }
}

function parse(root: string, rel: string): tsNamespace.SourceFile {
  const sf = ts.createSourceFile(rel, fs.readFileSync(path.join(root, rel), 'utf8'), ts.ScriptTarget.Latest, true)
  const diagnostics = (sf as any).parseDiagnostics
  if (!Array.isArray(diagnostics)) throw new Error(`cannot read the parse diagnostics of ${rel}: unsupported typescript`)
  if (diagnostics.length) {
    const first = diagnostics[0]
    const line = sf.getLineAndCharacterOfPosition(first.start ?? 0).line + 1
    throw new Error(`${rel} does not parse (${ts.flattenDiagnosticMessageText(first.messageText, ' ')}, line ${line})`)
  }
  return sf
}

/**
 * (repo-relative path, parsed tree) for each source file in the repo, skipping dot directories,
 * node_modules and generated output. A file that does not parse is a failure, not a skip: the files an
 * agent breaks are the ones whose stale callers this scan most needs to see. Only a path in `allowed`
 * is left out.
 */
export function* sources(root: string = REPO, allowed: Set<string> = new Set()): Generator<[string, tsNamespace.SourceFile]> {
  for (const rel of walk(root)) {
    try {
      yield [rel, parse(root, rel)]
    } catch (err) {
      if (allowed.has(rel)) continue
      throw err
    }
  }
}

/** For each source file in the repo, every name it defines, imports or uses. */
export function identifiers(root: string = REPO): Map<string, Set<string>> {
  const found = new Map<string, Set<string>>()
  for (const [rel, sf] of sources(root)) {
    const names = new Set<string>()
    const visit = (node: tsNamespace.Node) => {
      if (ts.isIdentifier(node) || ts.isPrivateIdentifier(node)) names.add(node.text)
      ts.forEachChild(node, visit)
    }
    visit(sf)
    found.set(rel, names)
  }
  return found
}

export function filesUsing(name: string, root: string = REPO): string[] {
  return [...identifiers(root)].filter(([, names]) => names.has(name)).map(([file]) => file).sort()
}

const DECLARATIONS = new Set([
  ts.SyntaxKind.FunctionDeclaration,
  ts.SyntaxKind.FunctionExpression,
  ts.SyntaxKind.MethodDeclaration,
  ts.SyntaxKind.MethodSignature,
  ts.SyntaxKind.PropertyDeclaration,
  ts.SyntaxKind.PropertySignature,
  ts.SyntaxKind.GetAccessor,
  ts.SyntaxKind.SetAccessor,
  ts.SyntaxKind.ClassDeclaration,
  ts.SyntaxKind.ClassExpression,
  ts.SyntaxKind.InterfaceDeclaration,
  ts.SyntaxKind.TypeAliasDeclaration,
  ts.SyntaxKind.EnumDeclaration,
  ts.SyntaxKind.EnumMember,
  ts.SyntaxKind.ModuleDeclaration,
])
const CONTAINERS = new Set([
  ts.SyntaxKind.ClassDeclaration,
  ts.SyntaxKind.ClassExpression,
  ts.SyntaxKind.InterfaceDeclaration,
  ts.SyntaxKind.EnumDeclaration,
  ts.SyntaxKind.ModuleDeclaration,
])

function enclosing(node: tsNamespace.Node): string {
  for (let p = node.parent; p; p = p.parent) {
    if (CONTAINERS.has(p.kind) && (p as any).name) return (p as any).name.getText()
  }
  return ''
}

function moduleOf(node: tsNamespace.Node): string {
  for (let p: tsNamespace.Node | undefined = node; p; p = p.parent) {
    if ((ts.isImportDeclaration(p) || ts.isExportDeclaration(p)) && p.moduleSpecifier) {
      return (p.moduleSpecifier as tsNamespace.StringLiteral).text
    }
  }
  return ''
}

function calleeOf(literal: tsNamespace.Node): string {
  // an object literal passed to a call is its named arguments: f({ name: 1 }) is a kwarg of f
  const parent = literal.parent
  if ((ts.isCallExpression(parent) || ts.isNewExpression(parent)) && parent.arguments?.includes(literal as tsNamespace.Expression)) {
    return parent.expression.getText()
  }
  return ''
}

function insideParameter(node: tsNamespace.Node): tsNamespace.ParameterDeclaration | undefined {
  for (let p: tsNamespace.Node | undefined = node.parent; p; p = p.parent) {
    if (ts.isParameter(p)) return p
    if (!ts.isObjectBindingPattern(p) && !ts.isArrayBindingPattern(p) && !ts.isBindingElement(p)) return undefined
  }
  return undefined
}

function functionName(parameter: tsNamespace.ParameterDeclaration): string {
  const fn = parameter.parent as any
  if (fn?.name) return fn.name.getText()
  // const f = (name) => ...
  const holder = fn?.parent
  return holder && ts.isVariableDeclaration(holder) ? holder.name.getText() : ''
}

function classify(node: tsNamespace.Identifier | tsNamespace.StringLiteral | tsNamespace.NoSubstitutionTemplateLiteral, name: string): Array<[Kind, string]> {
  const parent = node.parent
  const isString = !ts.isIdentifier(node)
  if (ts.isElementAccessExpression(parent) && parent.argumentExpression === node) return [['attr', parent.expression.getText()]]
  if (isString) {
    // a string only counts where it is a name: a property key
    if ((ts.isPropertyAssignment(parent) || ts.isBindingElement(parent)) && (parent as any).name === node) {
      return [['kwarg', calleeOf(parent.parent)]]
    }
    if (DECLARATIONS.has(parent.kind) && (parent as any).name === node) return [['def', enclosing(parent)]]
    return []
  }
  if (ts.isPropertyAccessExpression(parent) && parent.name === node) return [['attr', parent.expression.getText()]]
  if (ts.isQualifiedName(parent) && parent.right === node) return [['attr', parent.left.getText()]]
  if (ts.isImportSpecifier(parent) || ts.isExportSpecifier(parent) || ts.isImportClause(parent) || ts.isNamespaceImport(parent)) {
    return [['import', moduleOf(parent)]]
  }
  if (DECLARATIONS.has(parent.kind) && (parent as any).name === node) return [['def', enclosing(parent)]]
  if (ts.isParameter(parent) && parent.name === node) return [['param', functionName(parent)]]
  if (ts.isBindingElement(parent)) {
    if (parent.propertyName === node) return [['kwarg', '']]
    const parameter = insideParameter(parent)
    return [[parameter ? 'param' : 'name', parameter ? functionName(parameter) : '']]
  }
  if (ts.isPropertyAssignment(parent) && parent.name === node) return [['kwarg', calleeOf(parent.parent)]]
  if (ts.isShorthandPropertyAssignment(parent) && parent.name === node) {
    return [['kwarg', calleeOf(parent.parent)], ['name', '']]
  }
  if (ts.isJsxAttribute(parent) && parent.name === node) {
    const element = parent.parent.parent as tsNamespace.JsxOpeningLikeElement
    return [['kwarg', `<${element.tagName.getText()}>`]]
  }
  return [['name', '']]
}

/**
 * Every place the repo mentions `name` as a name (never in strings or comments). Each is
 * { file, line, kind, detail }; kind is one of:
 *   attr    x.name, x['name'], a.name in a type   detail: the receiver's source, e.g. "Result"
 *   name    a bare name (a variable, a type reference, a JSX tag)     detail: ""
 *   import  import { name } / export { name }     detail: the module
 *   def     a function, method, property, class, interface, type or enum member called name
 *           detail: the enclosing class, interface, enum or namespace, "" at module level
 *   param   a parameter called name               detail: the function it belongs to
 *   kwarg   f({ name: ... }), a destructuring key, <C name=...>   detail: the callee's source
 */
export function references(name: string, root: string = REPO): Reference[] {
  const found = new Map<string, Reference>()
  for (const [file, sf] of sources(root)) {
    const visit = (node: tsNamespace.Node) => {
      const text = ts.isIdentifier(node) ? node.text : ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node) ? node.text : null
      if (text === name) {
        const line = sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1
        for (const [kind, detail] of classify(node as any, name)) {
          found.set(`${file}:${line}:${kind}:${detail}`, { file, line, kind, detail })
        }
      }
      ts.forEachChild(node, visit)
    }
    visit(sf)
  }
  return [...found.values()].sort((a, b) => (a.file === b.file ? a.line - b.line || (a.kind < b.kind ? -1 : 1) : a.file < b.file ? -1 : 1))
}

/** The references as lines of "file:line kind detail". */
export function show(refs: Reference[]): string {
  return refs.map((r) => `${r.file}:${r.line} ${r.kind}${r.detail ? ` ${r.detail}` : ''}`).join('\n')
}

/**
 * The references on one line, a count per file ("src/a.ts x3 (line 63), tests/b.ts x5 (line 601)"). A runner
 * reports a failure's first line, and the grader clips it, so a message that names the callers must be short.
 */
export function byFile(refs: Reference[]): string {
  const files = new Map<string, Reference[]>()
  for (const r of refs) files.set(r.file, [...(files.get(r.file) ?? []), r])
  return [...files].map(([file, list]) => `${file} x${list.length} (line ${list[0].line})`).join(', ')
}

/**
 * A file the repo's runner collects as a test, or one that only tests use (anything under a tests, test or __tests__
 * directory). The hidden checks run over the agent's tree before its own tests are put back to the reference's, so a
 * correct solution may add a use of the new name in a test: a count or a file list of the new name is exact only
 * outside these files, and a stale use of the old name is a failure in every file.
 */
export function isTestFile(file: string): boolean {
  return /(?:^|\/)(?:__tests__|tests?)\//.test(file) || /\.(?:test|tests|spec)\.[cm]?[jt]sx?$/.test(file)
}

/** What `expected` has that `actual` lacks, one entry for each missing occurrence ([] when `actual` holds all of it, and maybe more). */
export function missingFrom(actual: string[], expected: string[]): string[] {
  const left = new Map<string, number>()
  for (const item of actual) left.set(item, (left.get(item) ?? 0) + 1)
  const missing: string[] = []
  for (const item of expected) {
    const have = left.get(item) ?? 0
    if (have > 0) left.set(item, have - 1)
    else missing.push(item)
  }
  return missing
}

/** The compiler's complaints for a tsconfig in the repo ("" when it type-checks): every caller, run or not. */
export function typecheck(project: string, root: string = REPO): string {
  const tsc = path.join(root, 'node_modules', 'typescript', 'bin', 'tsc')
  try {
    execFileSync(process.execPath, [tsc, '--noEmit', '-p', project], { cwd: root, encoding: 'utf8', stdio: 'pipe' })
    return ''
  } catch (err: any) {
    return String(err.stdout ?? '') + String(err.stderr ?? '') || String(err)
  }
}
