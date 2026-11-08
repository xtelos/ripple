import * as tsNamespace from 'typescript'
import { expect, it } from 'vitest'

import { byFile, filesUsing, isTestFile, references, sources, typecheck } from './check_helpers'
import * as superstruct from '../src'
import { Struct, createFrom, defaulted, number, object, string, StructError, trimmed } from '../src'

const ts: typeof tsNamespace = (tsNamespace as any).default ?? tsNamespace

// `create` is three different things: the exported function in src/struct.ts (renamed to createFrom), the
// method Struct.create (kept), and the boolean flag the fixtures under test/validation export and
// test/index.test.ts destructures out of them (kept: `export const create = true`, `const { create } = module`).
// The flag is spelled exactly like the function, and a fixture never imports the function, so the line that
// tells them apart is whether the file declares a `create` of its own. A file that does not and still writes a
// bare `create`, or imports one, is using the function that is gone. The uses are in the suite, and in
// examples/, which import it from the package name and which nothing runs.
function declaresItsOwn(sf: tsNamespace.SourceFile): boolean {
  let found = false
  const visit = (node: tsNamespace.Node) => {
    if ((ts.isVariableDeclaration(node) || ts.isBindingElement(node)) && ts.isIdentifier(node.name) && node.name.text === 'create') found = true
    ts.forEachChild(node, visit)
  }
  visit(sf)
  return found
}

function staleUses(): { file: string; line: number }[] {
  const left: { file: string; line: number }[] = []
  for (const [file, sf] of sources()) {
    const own = declaresItsOwn(sf)
    const visit = (node: tsNamespace.Node) => {
      if (ts.isIdentifier(node) && node.text === 'create') {
        const parent = node.parent
        const member = (ts.isPropertyAccessExpression(parent) && parent.name === node) || (ts.isQualifiedName(parent) && parent.right === node)
        const key = (ts.isPropertyAssignment(parent) || ts.isMethodDeclaration(parent) || ts.isPropertySignature(parent) || ts.isPropertyDeclaration(parent)) && (parent as any).name === node
        const imported = ts.isImportSpecifier(parent) || ts.isExportSpecifier(parent)
        const declared = (ts.isVariableDeclaration(parent) || ts.isBindingElement(parent)) && (parent as any).name === node
        const propertyName = ts.isBindingElement(parent) && parent.propertyName === node
        if (imported || (!member && !key && !declared && !propertyName && !own)) {
          left.push({ file, line: sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1 })
        }
      }
      ts.forEachChild(node, visit)
    }
    visit(sf)
  }
  return left
}

it('renamed, no alias, the method untouched', () => {
  const all = superstruct as any
  expect(typeof all.createFrom).toBe('function')
  expect('create' in all).toBe(false)
  expect(Object.keys(all)).not.toContain('create')
  expect(typeof Struct.prototype.create).toBe('function')
  expect(typeof (Struct.prototype as any).createFrom).toBe('undefined')
})

it('no stale uses', () => {
  const left = staleUses()
  if (left.length) throw new Error(`the function create is still used: ${byFile(left.map((l) => ({ ...l, kind: 'name' as const, detail: '' })))}`)
  const named = references('createFrom')
  const declared = named.filter((r) => r.kind === 'def' && !isTestFile(r.file))
  expect(declared.length).toBe(1)
  expect(declared[0].file).toBe('src/struct.ts')
  expect(declared[0].detail).toBe('')
  const imported = new Set(named.filter((r) => r.kind === 'import').map((r) => r.file))
  // the example nothing runs, and the suite (which may import it in a test of its own too: only the sources are exact)
  expect([...imported].filter((f) => !isTestFile(f)).sort()).toEqual(['examples/default-values.js'])
  expect([...imported].filter(isTestFile)).toEqual(expect.arrayContaining(['test/api/create.test.ts', 'test/index.test.ts']))
  expect(named.filter((r) => r.kind === 'name').map((r) => r.file)).toEqual(expect.arrayContaining(['examples/default-values.js', 'src/struct.ts', 'test/api/create.test.ts']))
})

it('the method, the fixtures flag and the local alias keep their names', () => {
  const method = references('create').filter((r) => r.kind === 'attr')
  expect(method.filter((r) => r.file === 'test/api/create.test.ts').length).toBeGreaterThanOrEqual(5)
  // Struct.create is the method every test of it calls, so a test may call it anywhere: the sources never do
  expect(method.filter((r) => !isTestFile(r.file))).toEqual([])
  const defs = references('create').filter((r) => r.kind === 'def').map((r) => `${r.file} ${r.detail}`)
  expect(defs).toEqual(['src/struct.ts Struct'])
  // the fixtures still say `export const create = true` and the harness still reads it
  const flagged = filesUsing('create').filter((f) => f.startsWith('test/validation/'))
  expect(flagged.length).toBeGreaterThanOrEqual(19)
  expect(filesUsing('create')).toContain('test/index.test.ts')
  // `import { createFrom as createValue }` in the harness keeps the name it is called by
  expect(filesUsing('createValue')).toContain('test/index.test.ts')
})

it('every use still works', () => {
  const User = object({ name: string(), age: defaulted(number(), 0) })
  expect(createFrom({ name: 'a' }, User)).toEqual({ name: 'a', age: 0 })
  expect(createFrom('  padded ', trimmed(string()))).toBe('padded')
  expect(() => createFrom({ name: 1 }, User)).toThrow(StructError)
  expect(() => createFrom(1, string(), 'custom')).toThrow('custom')
  expect(string().create('x')).toBe('x')
  expect(defaulted(number(), 5).create(undefined)).toBe(5)
  expect(() => string().create(1)).toThrow(StructError)
})

it('the repository still type-checks, the typings the suite never runs included', () => {
  // test/tsconfig.json reaches src through the imports; the root tsconfig.json is not used because it already
  // fails on the starting repo (no skipLibCheck, and the installed jest-mock types need a newer lib than tsc 4.9)
  expect(typecheck('test/tsconfig.json')).toBe('')
})
