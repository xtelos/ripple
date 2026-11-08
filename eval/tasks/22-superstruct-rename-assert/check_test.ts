import { expect, it } from 'vitest'

import { byFile, filesUsing, isTestFile, references, typecheck } from './check_helpers'
import * as superstruct from '../src'
import { Struct, assertValid, number, object, string } from '../src'

// `assert` is two functions: the exported function in src/struct.ts (renamed to assertValid) and the method
// Struct.assert (kept). Every use of the function is a bare name or an import specifier, the method is only
// ever reached as `x.assert(...)`, so a use is stale unless it is a member access. The uses are in the
// suite (test/api, test/index.test.ts, test/deprecated.test.ts), in test/typings, which only `tsc` reads,
// and in examples/, which nothing runs.
const MODULES_OTHER_THAN_US = new Set(['assert', 'node:assert'])
const stale = () =>
  references('assert').filter(
    (r) => r.kind === 'name' || r.kind === 'param' || r.kind === 'kwarg' || (r.kind === 'import' && !MODULES_OTHER_THAN_US.has(r.detail)) || (r.kind === 'def' && r.detail !== 'Struct'),
  )

it('renamed, no alias, the method untouched', () => {
  const all = superstruct as any
  expect(typeof all.assertValid).toBe('function')
  expect('assert' in all).toBe(false)
  expect(Object.keys(all)).not.toContain('assert')
  expect(typeof Struct.prototype.assert).toBe('function')
  expect(typeof (Struct.prototype as any).assertValid).toBe('undefined')
})

it('no stale uses', () => {
  const left = stale()
  if (left.length) throw new Error(`the function assert is still used: ${byFile(left)}`)
  const named = references('assertValid')
  const declared = named.filter((r) => r.kind === 'def')
  expect(declared.length).toBe(1)
  expect(declared[0].file).toBe('src/struct.ts')
  expect(declared[0].detail).toBe('')
  const imported = new Set(named.filter((r) => r.kind === 'import').map((r) => r.file))
  // the suite, the typings no runner executes, and the examples nothing runs
  expect(imported.has('test/api/assert.test.ts')).toBe(true)
  expect(imported.has('test/typings/any.ts')).toBe(true)
  expect(imported.has('examples/basic-validation.js')).toBe(true)
  expect([...imported].filter((f) => f.startsWith('examples/')).length).toBe(6)
  expect([...imported].filter((f) => f.startsWith('test/typings/')).length).toBeGreaterThanOrEqual(40)
})

it('the method and the local alias keep their names', () => {
  const method = references('assert').filter((r) => r.kind === 'attr')
  expect(method.length).toBeGreaterThanOrEqual(3)
  expect(method.filter((r) => r.file === 'test/api/assert.test.ts').length).toBeGreaterThanOrEqual(3)
  // Struct.assert is the method every test of it calls, so a test may call it anywhere: the sources never do
  expect(method.filter((r) => !isTestFile(r.file))).toEqual([])
  const owners = references('assert').filter((r) => r.kind === 'def').map((r) => `${r.file} ${r.detail}`)
  expect(owners).toEqual(['src/struct.ts Struct'])
  // `import { assertValid as assertValue }` in the harness keeps the name it is called by
  expect(filesUsing('assertValue')).toContain('test/index.test.ts')
})

it('every use still works', () => {
  const User = object({ name: string(), age: number() })
  expect(() => assertValid({ name: 'a', age: 1 }, User)).not.toThrow()
  expect(() => assertValid({ name: 'a', age: '1' }, User)).toThrow(superstruct.StructError)
  expect(() => assertValid(1, string(), 'custom')).toThrow('custom')
  expect(() => string().assert('x')).not.toThrow()
  expect(() => string().assert(1)).toThrow(superstruct.StructError)
})

it('the repository still type-checks, the typings the suite never runs included', () => {
  // test/tsconfig.json reaches src through the imports; the root tsconfig.json is not used because it already
  // fails on the starting repo (no skipLibCheck, and the installed jest-mock types need a newer lib than tsc 4.9)
  expect(typecheck('test/tsconfig.json')).toBe('')
})
